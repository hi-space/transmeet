# STT Keep-Warm Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution.

**Goal:** Keep one Qwen and one Nemotron model copy running so model selection does not incur another GPU startup.

**Architecture:** Set the Application Auto Scaling copy floor and SageMaker host floor to one. Disable the backend's independent idle shutdown with `STT_IDLE_SECONDS=0`, and make retained capacity the deployment default. A positive idle timeout and explicit `--scale-to-zero` preserve the optional cost-saving mode.

**Tech Stack:** Python, SageMaker inference components, Application Auto Scaling, ECS Fargate, AWS CDK.

**Spec:** User instruction in this conversation: “1 띄우고 굳이내리자ㅣ말자 새로 띄우는게 시간걸리면”. Applies to Qwen and Nemotron; continue the Korean/English comparison.

## Constraints

- Keep each existing endpoint and model; maintain at most one copy per provider.
- Preserve unrelated workspace changes and use the currently deployed backend image as the deployment baseline.
- Do not stop or release benchmark capacity after measurements finish.
- Record the actual results and the earlier startup failure separately.

## Review focus

- A long silent interval must not release a retained copy.
- The activity metric must remain active when automatic shutdown is disabled.
- Positive idle timeouts must retain the existing idle-release behavior.
- Deployment plans must expose the selected retention policy and backend setting.
- AWS auto scaling must not override the requested one-copy minimum.

## Implementation

- [x] Add failing retention tests to `backend/tests/test_stt_capacity.py`; verify an idle ready copy survives with timeout zero and its activity metric stays active.
- [x] Add the timeout guard to `backend/app/stt_capacity.py`, set the default in `backend/app/config.py`, and set the ECS environment in `infra/lib/transmeet-stack.ts`.
- [x] Test and update `backend/scripts/deploy_stt_scaling.py`: default to keep-warm, set `MinCapacity=1`, and retain explicit `--scale-to-zero`.
- [x] Run focused tests, the backend suite, and the infrastructure TypeScript build.
- [x] Update the two existing AWS scalable targets and deploy the narrow backend patch from the deployed image; verify service health and retained copies.
- [x] Finish and validate all Korean/English benchmark results, update the STT guide, and report the comparison and ongoing GPU cost.

## Operational update

The initial Qwen host request failed with AWS's generic service failure after
the model had no hardware to load onto. Use a new `warm-v1` endpoint
configuration with `MinInstanceCount=1`; prefer the previously working
`ml.g6.2xlarge` pool. Preserve the endpoint and component names. This remains
inside the hourly cost range already stated to the user. Migrate the existing
endpoint with the deployment script's explicit `--migrate` flag.

The warm migration also failed in US East with `InsufficientInstanceCapacity`.
The user approved moving regions, preferring Seoul and accepting Oregon.
Seoul catalog, quota, and deployment checks succeeded for both models. Keep the
Seoul copies running and retire the superseded US East allocation requests.

Per-provider routing uses `QWEN_ASR_REGION` and `NEMOTRON_ASR_REGION` for batch
inference, capacity operations, and regional activity metrics. The concurrent
custom Nemotron streaming work uses its separate `NEMOTRON_STREAMING_REGION`.
Empty backend overrides fall back to `REGION`; production CDK defaults for the
two batch models are Seoul. The deployment plan prints each selected model region.

The rollout guard now verifies `EndpointConfigName` after `InService`, because
AWS can roll back a failed update and report the old endpoint as in service.
The rollback test was observed failing before the guard was implemented.

## Verified outcome

- Seoul Qwen and Nemotron endpoints, model copies, and scaling floors are all 1.
- ECS task definition revision 27 uses the Seoul regions and `STT_IDLE_SECONDS=0`;
  the service rollout completed and its load balancer target is healthy.
- Both superseded US East endpoints have current/desired GPU counts 0 and
  current/desired model copy counts 0. No endpoint or model was deleted.
- All 14 benchmark conditions and 140 sample evaluations completed without an
  inference error. Input hashes, per-sample CER, and reported medians were
  recomputed and verified; English WER was added from the saved transcripts.
- Detailed evidence is in `benchmarks/stt-ko-en-2026-10-28/`.
