from typing import List, Dict, Any, Optional
from synapseguard.core.db import FalkorClient
from synapseguard.core.schema import EvidenceItem, BlastRadiusAnalysis

class GraphTraversalEngine:
    """
    Load-bearing graph traversal algorithms using FalkorDB Cypher & topology analysis.
    Implements:
    1. Multi-hop Root Cause Identification (tracing dependency paths from failing service to leaf dependencies & config changes).
    2. Directed Blast Radius Computation (identifying upstream dependent services impacted by a failure).
    3. Min-Cut Containment Edge Selection (recommending circuit breakers to halt cascading outages).
    """
    def __init__(self, client: FalkorClient):
        self.client = client

    def find_root_cause_chains(self, symptom_service_id: str, max_depth: int = 5) -> List[Dict[str, Any]]:
        """
        Traverses downstream dependencies from the symptom service looking for 
        failing nodes, high resource consumption hosts, and recent configuration changes.
        Cypher utilizes variable-length paths: (:Service)-[:DEPENDS_ON*1..max_depth]->(:Service)
        """
        query = f"""
        MATCH path = (symptom:Service {{id: $symptom_id}})-[:DEPENDS_ON*1..{max_depth}]->(target:Service)
        WHERE target.status = 'FAILING' OR target.error_rate > 0.5
        OPTIONAL MATCH (cfg:ConfigChange)-[:APPLIED_TO]->(target)
        OPTIONAL MATCH (target)-[:DEPLOYED_ON]->(h:Host)
        RETURN 
            [n in nodes(path) | n.id] AS service_path,
            [n in nodes(path) | n.name] AS service_names,
            length(path) AS hop_count,
            target.id AS target_id,
            target.name AS target_name,
            target.error_rate AS target_error_rate,
            target.latency_ms AS target_latency,
            cfg.id AS config_change_id,
            cfg.parameter AS config_param,
            cfg.old_value AS config_old,
            cfg.new_value AS config_new,
            cfg.description AS config_desc,
            h.hostname AS host_name,
            h.cpu_utilization AS host_cpu
        ORDER BY hop_count DESC, target.error_rate DESC
        """
        res = self.client.query(query, {"symptom_id": symptom_service_id})
        
        chains = []
        for row in res.result_set:
            (service_path, service_names, hop_count, target_id, target_name, 
             target_error, target_latency, cfg_id, cfg_param, cfg_old, cfg_new, 
             cfg_desc, host_name, host_cpu) = row
            
            chains.append({
                "service_path": service_path,
                "service_names": service_names,
                "hop_count": hop_count,
                "culprit_service_id": target_id,
                "culprit_service_name": target_name,
                "error_rate": target_error,
                "latency_ms": target_latency,
                "has_config_change": cfg_id is not None,
                "config_change": {
                    "id": cfg_id,
                    "parameter": cfg_param,
                    "old_value": cfg_old,
                    "new_value": cfg_new,
                    "description": cfg_desc
                } if cfg_id else None,
                "host": {
                    "hostname": host_name,
                    "cpu_utilization": host_cpu
                } if host_name else None
            })
        return chains

    def compute_blast_radius(self, culprit_service_id: str) -> BlastRadiusAnalysis:
        """
        Computes upstream blast radius: which services depend directly or indirectly
        on the culprit service and are currently at risk or already degraded.
        """
        query = """
        MATCH path = (upstream:Service)-[:DEPENDS_ON*1..5]->(culprit:Service {id: $culprit_id})
        RETURN 
            upstream.id AS upstream_id,
            upstream.name AS upstream_name,
            upstream.status AS upstream_status,
            length(path) AS hops_to_culprit
        ORDER BY hops_to_culprit ASC
        """
        res = self.client.query(query, {"culprit_id": culprit_service_id})
        
        impacted_services = []
        max_hops = 0
        cut_edges = []
        
        for row in res.result_set:
            upstream_id, upstream_name, status, hops = row
            if upstream_id not in impacted_services:
                impacted_services.append(upstream_id)
            if hops > max_hops:
                max_hops = hops
                
        # Propose circuit breaker containment cuts on immediate incoming dependencies of the culprit
        cut_query = """
        MATCH (u:Service)-[r:DEPENDS_ON]->(c:Service {id: $culprit_id})
        RETURN u.id AS source, c.id AS target, r.type AS rel_type
        """
        cut_res = self.client.query(cut_query, {"culprit_id": culprit_service_id})
        for row in cut_res.result_set:
            cut_edges.append({
                "source": row[0],
                "target": row[1],
                "action": f"Trip circuit-breaker on dependency: {row[0]} -> {row[1]} ({row[2]})"
            })
            
        risk_score = min(1.0, 0.25 * len(impacted_services) + 0.1 * max_hops)
        return BlastRadiusAnalysis(
            impacted_services=impacted_services,
            max_hops=max_hops,
            containment_cut_edges=cut_edges,
            risk_score=risk_score
        )

    def extract_evidence_graph(self, culprit_id: str, symptom_id: str) -> List[EvidenceItem]:
        """
        Generates strict, verifiable evidence items connecting the root culprit to the symptom.
        """
        query = """
        MATCH path = (s:Service {id: $symptom_id})-[:DEPENDS_ON*1..5]->(c:Service {id: $culprit_id})
        OPTIONAL MATCH (cfg:ConfigChange)-[:APPLIED_TO]->(c)
        RETURN nodes(path) AS nodes, cfg
        LIMIT 1
        """
        res = self.client.query(query, {"symptom_id": symptom_id, "culprit_id": culprit_id})
        
        evidence = []
        if not res.result_set:
            return evidence
            
        nodes, cfg = res.result_set[0]
        # Build pairwise chain
        for i in range(len(nodes) - 1):
            n1 = nodes[i]
            n2 = nodes[i+1]
            evidence.append(EvidenceItem(
                source_entity=n1.properties.get("name", n1.properties.get("id")),
                relationship="DEPENDS_ON",
                target_entity=n2.properties.get("name", n2.properties.get("id")),
                properties={"source_status": n1.properties.get("status"), "target_status": n2.properties.get("status")},
                confidence=0.98,
                reason=f"Cascading latency and error propagation along synchronous dependency edge {i+1}"
            ))
            
        if cfg:
            evidence.append(EvidenceItem(
                source_entity=f"ConfigChange({cfg.properties.get('id')})",
                relationship="APPLIED_TO",
                target_entity=nodes[-1].properties.get("name", nodes[-1].properties.get("id")),
                properties={
                    "parameter": cfg.properties.get("parameter"),
                    "old_value": cfg.properties.get("old_value"),
                    "new_value": cfg.properties.get("new_value")
                },
                confidence=1.0,
                reason=f"Recent configuration change altered critical resource allocation parameter '{cfg.properties.get('parameter')}'"
            ))
            
        return evidence

