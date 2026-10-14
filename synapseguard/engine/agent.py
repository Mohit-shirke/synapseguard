import os
import json
import httpx
from typing import Dict, Any, List, Optional
from synapseguard.core.db import FalkorClient
from synapseguard.core.schema import ContainmentPlan, EvidenceItem, BlastRadiusAnalysis
from synapseguard.engine.traversal import GraphTraversalEngine
from synapseguard.engine.memory import GraphMemoryCoordinator

class SynapseAgent:
    """
    AI Agent that reasons over FalkorDB connected topology data.
    Takes an incident, retrieves multi-hop graph context, synthesizes
    root causes, checks blast radius, recalls past graph memories,
    and generates executable containment actions.
    Uses fast local Ollama model with deterministic algorithmic fallback for zero-cost resilience.
    """
    def __init__(self, client: FalkorClient, model_name: Optional[str] = None, ollama_url: Optional[str] = None):
        self.client = client
        self.traversal = GraphTraversalEngine(client)
        self.memory = GraphMemoryCoordinator(client)
        self.model_name = model_name or os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
        self.ollama_url = ollama_url or os.getenv("OLLAMA_URL", "http://localhost:11434")

    def analyze_and_remediate(self, incident_id: str) -> ContainmentPlan:
        """
        End-to-end incident investigation workflow:
        1. Fetch incident from FalkorDB.
        2. Execute multi-hop traversal to isolate candidate root causes.
        3. Formulate graph-grounded evidence chain.
        4. Query persistent FalkorDB memory for past verified incident playbooks.
        5. Calculate blast radius and circuit-breaker cut edges.
        6. Invoke LLM to generate reasoned narrative and rollback playbook.
        7. Persist resolution plan into graph memory for cross-session recall.
        """
        # 1. Fetch Incident
        inc_res = self.client.query(
            "MATCH (i:Incident {id: $id})-[:OBSERVED_ON]->(s:Service) RETURN i.title, i.severity, s.id, s.name",
            {"id": incident_id}
        )
        if not inc_res.result_set:
            raise ValueError(f"Incident {incident_id} not found in FalkorDB")
            
        title, severity, symptom_id, symptom_name = inc_res.result_set[0]
        
        # 2. Multi-hop traversal to identify root causes
        chains = self.traversal.find_root_cause_chains(symptom_id)
        if not chains:
            return ContainmentPlan(
                plan_id=f"plan-{incident_id}",
                root_cause_service=symptom_id,
                root_cause_explanation="No downstream dependency failures detected. Symptom is isolated to host/service itself.",
                evidence_chain=[],
                blast_radius=BlastRadiusAnalysis(impacted_services=[symptom_id], max_hops=0, containment_cut_edges=[], risk_score=0.2),
                remediation_actions=["Restart symptom service container", "Inspect local host metrics"],
                graph_grounded=True
            )
            
        top_chain = chains[0]
        culprit_id = top_chain["culprit_service_id"]
        culprit_name = top_chain["culprit_service_name"]
        
        # 3. Evidence Chain & Blast Radius
        evidence_chain = self.traversal.extract_evidence_graph(culprit_id, symptom_id)
        blast_radius = self.traversal.compute_blast_radius(culprit_id)
        
        # 4. Recall Past Resolutions from Graph Memory
        past_memories = self.memory.recall_past_resolutions(culprit_id)
        
        # 5. Synthesize with LLM or Fallback
        explanation, remediation = self._generate_ai_reasoning(
            incident_title=title,
            symptom_name=symptom_name,
            culprit_name=culprit_name,
            hop_count=top_chain["hop_count"],
            service_path=" -> ".join(top_chain["service_names"]),
            config_change=top_chain.get("config_change"),
            blast_radius=blast_radius,
            past_memories=past_memories
        )
        
        # 6. Persist this resolution into Graph Memory for cross-session continuity
        if remediation:
            self.memory.record_resolution(incident_id, culprit_id, remediation[0])
        
        return ContainmentPlan(
            plan_id=f"plan-{incident_id}",
            root_cause_service=culprit_name,
            root_cause_explanation=explanation,
            evidence_chain=evidence_chain,
            blast_radius=blast_radius,
            remediation_actions=remediation,
            graph_grounded=True
        )

    def _generate_ai_reasoning(self, incident_title: str, symptom_name: str, culprit_name: str, 
                               hop_count: int, service_path: str, config_change: Optional[dict], 
                               blast_radius: BlastRadiusAnalysis, past_memories: List[dict]):
        """
        Calls Ollama LLM with strict 6-second timeout fallback.
        Incorporates recalled historical graph memories into prompt context.
        """
        cfg_text = "No recent configuration changes."
        if config_change and config_change.get("id"):
            cfg_text = (f"Config change {config_change['id']}: altered parameter '{config_change['parameter']}' "
                        f"from '{config_change['old_value']}' to '{config_change['new_value']}'. Description: {config_change['description']}")
            
        mem_text = "No prior incident memories for this service."
        if past_memories:
            mem_text = f"Historical incident memory confirms previous success with: {past_memories[0]['action_taken']}"

        prompt = f"""
You are SynapseGuard, an autonomous site reliability agent reasoning over a FalkorDB microservice graph.
An incident occurred: '{incident_title}'
Observed Symptom: Service '{symptom_name}' is throwing 504 errors.
Graph Traversal Path ({hop_count} hops): {service_path}
Identified Root Cause: Service '{culprit_name}'.
Root Cause Evidence: {cfg_text}
Recalled Graph Memory: {mem_text}
Upstream Impacted Services: {blast_radius.impacted_services}

Provide:
1. A concise technical explanation (2 sentences) explaining how the failure propagated through the graph from the root cause to the symptom service.
2. A list of 3 concrete, prioritized remediation steps.
Respond ONLY with valid JSON having keys: 'explanation' and 'remediation_actions'.
"""
        try:
            with httpx.Client(timeout=6.0) as http_client:
                resp = http_client.post(
                    f"{self.ollama_url}/api/generate",
                    json={"model": self.model_name, "prompt": prompt, "stream": False, "format": "json"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    parsed = json.loads(data.get("response", "{}"))
                    if "explanation" in parsed and "remediation_actions" in parsed:
                        return (parsed["explanation"], parsed["remediation_actions"])
        except Exception:
            pass  # Fallback seamlessly
            
        # Deterministic fallback logic guaranteed to never crash and run in < 1ms
        explanation = (
            f"FalkorDB graph traversal isolated root cause {hop_count} hops downstream at '{culprit_name}'. "
            f"Cascading latency and timeouts propagated through dependency path: {service_path}. "
            f"Evidence points to parameter alteration on {culprit_name}."
        )
        remediation = [
            f"Immediately revert parameter '{config_change['parameter']}' to '{config_change['old_value']}' on {culprit_name}" if config_change else f"Restart {culprit_name}",
            f"Execute circuit-breaker containment on immediate upstream dependency to stop cascading retries",
            f"Verify error rate recovery on {symptom_name}"
        ]
        return explanation, remediation
