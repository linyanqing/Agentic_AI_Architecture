```mermaid
stateDiagram-v2
    direction TB

    classDef awsState fill:#f8fafc,stroke:#94a3b8,stroke-width:2px,color:#334155;
    classDef choiceState fill:#fffbeb,stroke:#d97706,stroke-width:2px,color:#78350f;
    classDef errorBranch fill:#fff5f5,stroke:#dc2626,stroke-width:2px,color:#7f1d1d;
    classDef endState fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#14532d;

    [*] --> Intent_Router_Node : Start Execution
    
    state "Intent Router Node (Task)" as Intent_Router_Node
    class Intent_Router_Node awsState;

    Intent_Router_Node --> Route_After_Intent

    state "Route After Intent (Choice)" as Route_After_Intent
    class Route_After_Intent choiceState;
    
    Route_After_Intent --> Simple_Compute_Node : [$.complexity == 'SIMPLE']
    Route_After_Intent --> Primary_Provisioned_Node : [$.complexity == 'COMPLEX']

    state "Simple Compute Node (Task)" as Simple_Compute_Node
    class Simple_Compute_Node awsState;
    
    state "Primary Provisioned Node (Task)" as Primary_Provisioned_Node
    class Primary_Provisioned_Node awsState;

    Simple_Compute_Node --> Success_State

    Primary_Provisioned_Node --> Success_State : Normal Exec Path

    %% Native Catch block handling
    Primary_Provisioned_Node --> Fallback_Serverless_Node : Catch [Throttling / 503 Exception]
    class Fallback_Serverless_Node errorBranch;

    state "Fallback Serverless Node (Task)" as Fallback_Serverless_Node
    
    Fallback_Serverless_Node --> Success_State

    state "Success State (Succeed)" as Success_State
    class Success_State endState;
    
    Success_State --> [*]