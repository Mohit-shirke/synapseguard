from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class ServiceNode(BaseModel):
    id: str
    name: str
    tier: str  # frontend, api-gateway, core-service, cache, database
    error_rate: float = 0.0
    latency_ms: float = 0.0
    status: str = "HEALTHY" # HEALTHY, DEGRADED, FAILING

class IncidentNode(BaseModel):
    id: str
    title: str
    severity: str # CRITICAL, HIGH, MEDIUM, LOW
    timestamp: str
    target_service_id: str

class ConfigChangeNode(BaseModel):
    id: str
    author: str
    description: str
    parameter: str
    old_value: str
    new_value: str
    timestamp: str

class HostNode(BaseModel):
    id: str
    hostname: str
    cpu_utilization: float
    memory_utilization: float
    zone: str

class EvidenceItem(BaseModel):
    source_entity: str
    relationship: str
    target_entity: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    confidence: float
    reason: str

class BlastRadiusAnalysis(BaseModel):
    impacted_services: List[str]
    max_hops: int
    containment_cut_edges: List[Dict[str, str]]
    risk_score: float

class ContainmentPlan(BaseModel):
    plan_id: str
    root_cause_service: str
    root_cause_explanation: str
    evidence_chain: List[EvidenceItem]
    blast_radius: BlastRadiusAnalysis
    remediation_actions: List[str]
    graph_grounded: bool = True

