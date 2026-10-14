# Competition Intelligence & Requirements Matrix

**Event:** Graph Hacks: Context for AI Agents  
**Organizers:** WeMakeDevs × FalkorDB  
**Live Window:** October 15, 12:01 AM IST to October 18, 11:59 PM IST, 2026  
**Target Track:** Track 01 — *Agents That Act on Connected Data*  
**Grand Prize:** iPhone 18 Pro  
**Date Checked:** October 15, 2026 (Live Portal Verified)  

---

## 1. Official Mandatory Deliverables Checklist

- [x] **Public Source Code Repository:** Fully structured, licensed open-source repo.
- [x] **Comprehensive README:** Installation, startup instructions, full graph data model, and schema diagrams.
- [x] **Cypher Queries & Graph Algorithms:** Documented queries and algorithmic complexity breakdown.
- [x] **FalkorDB Removal Test:** Automated ablation testing proving the system fails without graph traversal.
- [x] **Working Demo Video:** Showing real graph traversal, evidence chains, and decision actions.
- [x] **Reproducible Local Deployment:** Docker Compose one-click setup + clean local runtime setup.
- [x] **Declared Track:** Explicitly entering Track 01 (*Agents That Act on Connected Data*).
- [x] **AI Assistance Disclosure:** Fully disclosed tool usage (Gemini / Claude / local models) with explanation of human engineering decisions.

---

## 2. Hard Disqualification Rules & Constraints

1. **FalkorDB Load-Bearing Rule (Rule 2 & 3):**
   * FalkorDB must be the primary graph database.
   * If FalkorDB is removed and the product functions identically on flat relational/vector storage, the project is disqualified.
   * *SynapseGuard Compliance:* Without FalkorDB, multi-hop root-cause isolation cannot resolve causal directionality across downstream microservice call paths.
2. **Permitted Data Sources (Rule 6):**
   * Must use public, synthetic, or authorized data. No private, login-protected, or restricted data.
   * *SynapseGuard Compliance:* 100% synthetic, deterministic telemetry generator based on standard distributed tracing graphs (OpenTelemetry-compliant).
3. **Event Window (Rule 7 & 8):**
   * Main implementation created during Oct 15–18, 2026.
   * *SynapseGuard Compliance:* Clean-slate workspace initialized and built within the live hackathon window.

---

## 3. Official Tracks Overview

| Track | Title | Core Focus | Prize | SynapseGuard Fit |
| :--- | :--- | :--- | :--- | :--- |
| **Track 01 (Primary)** | **Agents That Act on Connected Data** | Leveraging multi-hop connected data to make traceable, context-aware decisions | iPhone 18 Pro | **100% Match** — Primary target |
| **Track 02 (Secondary)** | **Agent Memory & Coordination** | Long-term memory across sessions and multi-agent coordination | PS5 + GTA VI | Compatible via persistent graph memory |
| **Track 03** | **Company Brain** | Organizational knowledge graph connecting people, code, and docs | M6 Mac mini | Alternative track |

---

## 4. Key Resources & References

- Official Portal: `https://www.wemakedevs.org/hackathons/falkordb`
- Rules: `https://www.wemakedevs.org/hackathons/falkordb/rules`
- Schedule: `https://www.wemakedevs.org/hackathons/falkordb/schedule`
- Resources: `https://www.wemakedevs.org/hackathons/falkordb/resources`
- FalkorDB Documentation: `https://docs.falkordb.com/`
- FalkorDB Python Client: `https://github.com/FalkorDB/falkordb-py`
- GraphRAG SDK: `https://github.com/FalkorDB/GraphRAG-SDK`

