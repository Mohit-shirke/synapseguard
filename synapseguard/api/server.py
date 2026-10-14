from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List
from synapseguard.core.db import FalkorClient
from synapseguard.core.schema import ContainmentPlan, BlastRadiusAnalysis
from synapseguard.data.seed import seed_telemetry_topology
from synapseguard.engine.agent import SynapseAgent
from synapseguard.engine.traversal import GraphTraversalEngine
from synapseguard.engine.memory import GraphMemoryCoordinator

app = FastAPI(
    title="SynapseGuard API",
    description="Autonomous Graph-Grounded Incident Causality & Containment Agent powered by FalkorDB",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = FalkorClient()
traversal = GraphTraversalEngine(client)
memory = GraphMemoryCoordinator(client)
agent = SynapseAgent(client)

@app.get("/health")
def health_check():
    try:
        res = client.query("RETURN 1")
        return {"status": "ok", "falkordb_connected": True}
    except Exception as e:
        return {"status": "degraded", "falkordb_connected": False, "error": str(e)}

@app.post("/api/reset-and-seed")
def reset_and_seed():
    """Deterministic reset and seeding of the microservice failure topology."""
    try:
        res = seed_telemetry_topology(client)
        return {"message": "Graph topology initialized successfully", "details": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/graph")
def get_graph_data():
    """Returns nodes and edges formatted for visual D3 / Cytoscape graph rendering."""
    try:
        nodes_res = client.query("MATCH (n) RETURN id(n) as internal_id, labels(n)[0] as label, properties(n) as props")
        edges_res = client.query("MATCH (a)-[r]->(b) RETURN id(a) as source, id(b) as target, type(r) as type, properties(r) as props")
        
        nodes = []
        for row in nodes_res.result_set:
            internal_id, label, props = row
            nodes.append({
                "id": str(internal_id),
                "label": label,
                "name": props.get("name", props.get("hostname", props.get("id", str(internal_id)))),
                "properties": props
            })
            
        edges = []
        for row in edges_res.result_set:
            source, target, rel_type, props = row
            edges.append({
                "source": str(source),
                "target": str(target),
                "type": rel_type,
                "properties": props
            })
            
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/incidents")
def list_incidents():
    """Lists active incidents currently tracked in FalkorDB."""
    try:
        res = client.query("MATCH (i:Incident)-[:OBSERVED_ON]->(s:Service) RETURN i.id, i.title, i.severity, i.timestamp, s.id, s.name")
        incidents = []
        for row in res.result_set:
            incidents.append({
                "id": row[0],
                "title": row[1],
                "severity": row[2],
                "timestamp": row[3],
                "service_id": row[4],
                "service_name": row[5]
            })
        return incidents
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/investigate/{incident_id}", response_model=ContainmentPlan)
def investigate_incident(incident_id: str):
    """Triggers autonomous agent reasoning over the graph for a specific incident."""
    try:
        plan = agent.analyze_and_remediate(incident_id)
        return plan
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/memories/{culprit_id}")
def get_memories(culprit_id: str):
    """Retrieves persistent cross-session resolution memories for a service."""
    try:
        return memory.recall_past_resolutions(culprit_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/blast-radius/{service_id}", response_model=BlastRadiusAnalysis)
def get_blast_radius(service_id: str):
    """Calculates upstream blast radius and circuit breaker cut edges."""
    try:
        return traversal.compute_blast_radius(service_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
