# Architecture Diagrams — Process Mining RCA Platform

Rendered Mermaid diagrams. View in VS Code (Markdown Preview / Mermaid plugin),
on GitHub, or paste into https://mermaid.live to export a high-res PNG/SVG for
the slide deck.

---

## 1. Platform architecture (the big picture)

```mermaid
flowchart TB
    subgraph SRC["1 - Telemetry Sources (multi-pillar)"]
        direction LR
        A["Appian x3<br/>process / integration / task-errors"]
        M["Mule<br/>integration runtime"]
        P["Prometheus<br/>infra metrics"]
        F["Fluentd<br/>infra logs"]
    end

    subgraph T1["Tier 1 - Ingest &amp; Detect"]
        RD["Readers"] --> EL[("event_log")]
        EL --> AZ["Analyzers<br/>(rule-based)"] --> FN[("finding")]
    end

    subgraph T2["Tier 2 - Correlate &amp; Diagnose"]
        GR["Incident Grouper<br/>(deterministic union-find)"] --> IC(["Incident<br/>(in memory)"])
        IC --> INV["LLM RCA Investigator"]
        INV --> RC[("root_cause_report")]
    end

    subgraph T3["Tier 3 - Remediate (human-gated)"]
        RC --> RA[("remediation_action")]
        RA --> LG["LangGraph workflow<br/>validate - execute - verify - report"]
    end

    subgraph EXT["External systems"]
        LLM(["Azure OpenAI"])
        SN(["ServiceNow"])
        UI(["Streamlit UI"])
        HI[("historical_incident")]
    end

    SRC --> RD
    FN --> GR
    INV <-->|prompt + answer| LLM
    HI -.->|precedent &gt;= 0.60 conf| INV
    RC -->|file ticket| SN
    SN -.->|nightly sync of resolved| HI
    UI -.->|operator approves| RA
    LG -.->|journal comments| SN
    RC -.->|view + approve| UI

    classDef src fill:#e6f2ff,stroke:#1c66c9,color:#0a2540;
    classDef t1 fill:#e8f7ee,stroke:#1f9d57,color:#0a2540;
    classDef t2 fill:#fff3e0,stroke:#e67e22,color:#0a2540;
    classDef t3 fill:#fde8ec,stroke:#d6336c,color:#0a2540;
    classDef ext fill:#f3e8ff,stroke:#7c3aed,color:#0a2540;
    class A,M,P,F src;
    class RD,EL,AZ,FN t1;
    class GR,IC,INV,RC t2;
    class RA,LG t3;
    class LLM,SN,UI,HI ext;
```

---

## 2. End-to-end flow (what happens per incident)

```mermaid
flowchart LR
    L["Logs from<br/>all sources"] --> E[("event_log")]
    E --> FF[("finding<br/>problems flagged")]
    FF --> I(["Incident<br/>grouped across sources"])
    I --> D["Diagnose (LLM)<br/>+ precedent lookup"]
    D --> R[("root_cause_report<br/>cause + fix + cited precedent")]
    R --> TK(["ServiceNow ticket"])
    R --> AX(["Recommended actions"])
    AX --> AP{"Operator<br/>approves?"}
    AP -->|yes| EX["LangGraph:<br/>validate-execute-verify"]
    AP -->|no| SK["tracked, not run"]
    EX -.->|comments| TK

    classDef store fill:#eef2f7,stroke:#5b6b7b,color:#0a2540;
    classDef act fill:#fff3e0,stroke:#e67e22,color:#0a2540;
    class E,FF,R store;
    class D,EX act;
```

---

## 3. The runner loop (production = always-on)

```mermaid
flowchart LR
    START(["runner.py<br/>every ~30s"]) --> C1["1 INGEST<br/>sources - event_log"]
    C1 --> C2["2 DISPATCH<br/>analyzers - finding"]
    C2 --> C3["3 INVESTIGATE<br/>group - LLM - report"]
    C3 --> C4["4 REMEDIATE<br/>run APPROVED actions only"]
    C4 --> START

    classDef cyc fill:#e8f7ee,stroke:#1f9d57,color:#0a2540;
    class C1,C2,C3,C4 cyc;
```

---

## How to export for the slide deck
- **VS Code:** open this file, use the Markdown/Mermaid preview, right-click the
  diagram -> copy/export image.
- **mermaid.live:** paste a diagram block, then "Actions -> PNG/SVG" (high-res).
- Drop the exported PNG onto the "Architecture" slide in `DEMO_DECK.md`.
