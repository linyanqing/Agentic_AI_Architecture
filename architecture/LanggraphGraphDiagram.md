```mermaid
stateDiagram-v2
    direction TB

    classDef routerNode fill:#edf2f7,stroke:#4a5568,stroke-width:2px,color:#2d3748;
    classDef stableCompute fill:#e6fffa,stroke:#319795,stroke-width:2px,color:#234e52;
    classDef primaryCompute fill:#ebf8ff,stroke:#3182ce,stroke-width:2px,color:#2b6cb0;
    classDef failurePath fill:#fff5f5,stroke:#e53e3e,stroke-width:2px,color:#9b2c2c;
    classDef entryExit fill:#2d3748,stroke:#1a202c,stroke-width:2px,color:#fff;

    [*] --> router_node : app invoke initial state

    state "1. Router Node (amazon.nova-micro-v1:0)" as router_node
    class router_node routerNode;

    router_node --> route_after_intent : Evaluate complexity intent

    state route_after_intent <<choice>>
    route_after_intent --> simple_node : Intent is SIMPLE
    route_after_intent --> primary_node : Intent is COMPLEX

    state "2a. Simple Compute Node (amazon.nova-micro-v1:0)" as simple_node
    class simple_node stableCompute;

    state "2b. Primary Provisioned Node (Sydney Dedicated 1 MU)" as primary_node
    class primary_node primaryCompute;

    primary_node --> route_after_primary : Success path

    primary_node --> fallback_node : Throttling Exception or 503 Fault
    class fallback_node failurePath;

    state "3. Fallback Serverless Node (us.anthropic.claude-3-5-sonnet)" as fallback_node

    state route_after_primary <<choice>>
    route_after_primary --> END : use fallback is False

    simple_node --> END
    fallback_node --> END

    state "END (Payload Extraction)" as END
    class END entryExit;
