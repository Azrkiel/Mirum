# <TASK_ID> — implementation handoff

State: not_started / in_progress / blocked / complete  
Task card: ../tasks/<TASK_ID>-<slug>.md  
Branch: engineering/checked-design-v1  
Starting commit: <actual SHA>  
Tested implementation commit: <actual SHA; do not use a self-referential final handoff SHA>  
Date: <actual date>

## Goal and accepted result

Describe the intended behavior and whether the task's acceptance conditions were met.

## Changes and integration surface

List changed modules, interfaces/schema versions, migrations/configuration, relevant files and compatibility behavior. Explain what downstream tasks should call and what remains provisional.

## Validation actually performed

| Command / observation | Runtime / source revision | Actual result | Evidence location |
|---|---|---|---|
| Not run yet | — | — | — |

Record exit codes/counts, native versus mocked/provider/physical scope and artifact hashes where applicable. Do not put credentials or private prompts in committed evidence.

## Acceptance checklist

Copy the task card's acceptance conditions and mark each passed, failed, not_evaluated or blocked with evidence.

## Remaining risks and blockers

Record concrete unresolved issues, their impact and exact missing dependency/input. A blocked mandatory condition prevents complete status.

## Integration and next steps

Identify next eligible task IDs, consumer interfaces, required follow-ups and any checks to repeat after integration. Update ../STATUS.md and relevant ../ACCEPTANCE.md rows. Include rollback notes for state/configuration migrations.
