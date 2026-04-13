# GenUI Dataset Pipeline

Generative UI 모바일 위젯 시나리오 데이터를 **4단계 파이프라인**으로 생성하고 CSV로 누적 저장하는 스크립트 모음입니다.

## 한눈에 보는 전체 흐름 🧭

> 현재 4단계 구조를 기준으로, 전체 흐름을 빠르게 파악할 수 있게 요약했습니다 🙂

```mermaid
flowchart LR
    A[1단계\n시나리오 생성\nmobile_widget_scenarios.csv]
    B[2단계\nTool Call 생성\nmobile_widget_tool_calls.csv]
    C[3단계\n예시 JSON 생성\nmobile_widget_example_json.csv]
    D[4단계\nGenUI TSX 생성\nmobile_widget_genui_tsx.csv]

    A --> B --> C --> D
```

### 예상 생성량 계산

기본값 기준으로, 각 단계의 예상 생성 건수는 아래처럼 계산할 수 있습니다.

| 파라미터 | 의미 | 기본값 |
|---|---|---:|
| `categories` | 생성 대상 카테고리 수 | 11 |
| `target_per_category` | 카테고리당 1단계 목표 시나리오 수 | 5 |
| `max_items_per_scenario` | 시나리오당 2단계 tool call 최대 개수 | 3 |
| `variants_per_scenario` | 시나리오당 3단계 JSON variant 개수 | 3 |
| `samples_per_input` | 3단계 입력 1건당 4단계 샘플 수 | 3 |

#### 단계별 계산 (기본값)

- **1단계**: `11 × 5 = 55`
- **2단계**: `55 × 3 = 165`
- **3단계**: `55 × 3 = 165`  
  (※ 3단계는 **tool call 수가 아니라 시나리오 수(1단계 결과)**를 기준으로 variant를 만듭니다.)
- **4단계**: `165 × 3 = 495`

#### 일반식 (S1~S4)

- `S1 = categories × target_per_category`
- `S2 = S1 × max_items_per_scenario`
- `S3 = S1 × variants_per_scenario`
- `S4 = S3 × samples_per_input`

CLI 옵션을 바꿨을 때는 위 식에 값만 대입하면 즉시 재계산할 수 있습니다.

#### 관계 다이어그램 (1→2, 1→3, 3→4)

```mermaid
flowchart LR
    S1["S1: 1단계 시나리오 수\n= categories × target_per_category"]
    S2["S2: 2단계 tool call 수\n= S1 × max_items_per_scenario"]
    S3["S3: 3단계 JSON variant 수\n= S1 × variants_per_scenario\n(시나리오 기준)"]
    S4["S4: 4단계 GenUI 샘플 수\n= S3 × samples_per_input"]

    S1 --> S2
    S1 --> S3
    S3 --> S4
```

### 단계별 입력/출력 요약 (공통 스키마 용어 기준)

| 단계 | 스크립트 | 입력 | 출력 |
|---|---|---|---|
| 1 | `generate_mobile_widget_scenarios.py` | 카테고리 목록, 모델 | `mobile_widget_scenarios.csv` (`STAGE1_FIELDS`) |
| 2 | `generate_widget_tool_calls.py` | 1단계 CSV (`STAGE1_REQUIRED_FIELDS`) | `mobile_widget_tool_calls.csv` (`STAGE2_FIELDS`) |
| 3 | `generate_widget_example_json.py` | 1단계 CSV (`STAGE1_REQUIRED_FIELDS`) + 2단계 CSV (`STAGE2_REQUIRED_FIELDS`) | `mobile_widget_example_json.csv` (`STAGE3_FIELDS`) |
| 4 | `generate_genui_tsx.py` | 3단계 CSV (`STAGE3_REQUIRED_FIELDS`) | `mobile_widget_genui_tsx.csv` (`STAGE4_FIELDS`) |

> 용어 표준: `tool_call`(단수) / `tool_calls`(복수)를 표준 용어로 사용합니다.
>
> 조인 키 표준: stage2~stage3 매칭은 `SCENARIO_JOIN_KEY_FIELDS` (`scenario_created_at`, `scenario_model`, `category`, `scenario`)를 사용합니다.
>
> 스키마 변경 원칙: 컬럼/필수 컬럼/조인 키를 바꿀 때는 `common/schemas.py` 1곳을 먼저 수정하세요.

---

## 빠른 실행 (전체 파이프라인) ⚡

### 요구 사항

- Python 3.10+
- `openai` 패키지

```bash
pip install openai
```

### 환경변수 설정(선택)

```bash
export VLLM_BASE_URL="http://localhost:8000/v1"
export VLLM_MODEL="Qwen/Qwen2.5-7B-Instruct"
export VLLM_API_KEY="EMPTY"
```

### 순차 실행 예시

```bash
# 1단계
python generate_mobile_widget_scenarios.py

# 2단계
python generate_widget_tool_calls.py

# 3단계
python generate_widget_example_json.py

# 4단계
python generate_genui_tsx.py
```

---

## 자세한 사용법 (클릭해서 펼치기) 📘

### 1단계: 시나리오 생성 🧩

<details>
<summary><strong>자세히 보기</strong></summary>

`generate_mobile_widget_scenarios.py`는 vLLM(OpenAI 호환 API)로 카테고리별 시나리오를 생성합니다.

#### 주요 동작

- 카테고리별로 시나리오 생성 요청
- 모델 응답 1회당 기본 5개 시나리오 생성
- 카테고리별 기존 시나리오 수를 확인해 `target` 미만일 때만 **부족분만 추가 생성**
- 예시 목록/기존 시나리오와 중복되지 않도록 프롬프트 제약 + 후처리 필터링
- 추상적 주제(예: `hotel reservation`) 대신 화면/의도 단위의 구체 시나리오를 유도
  - 예: `hotel search results`, `hotel room comparison`, `hotel booking payment`, `hotel booking confirmation`
- CSV 저장 컬럼
  - `created_at`
  - `model`
  - `prompt`
  - `category`
  - `scenario`

#### 실행 방법

기본 실행:

```bash
python generate_mobile_widget_scenarios.py \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct \
  --csv-path mobile_widget_scenarios.csv
```

카테고리 직접 지정:

```bash
python generate_mobile_widget_scenarios.py --categories 쇼핑 음악 미디어 캘린더 여행 요리 운동 금융 생산성 커뮤니케이션 헬스케어
```

#### 옵션

- `--csv-path`: 출력 CSV 경로 (기본: `mobile_widget_scenarios.csv`)
- `--base-url`: vLLM OpenAI 호환 API URL (기본: `http://localhost:8000/v1`)
- `--api-key`: API 키 (기본: `VLLM_API_KEY` 또는 `EMPTY`)
- `--model`: 생성 모델명
- `--temperature`: 샘플링 온도 (기본: `0.8`)
- `--responses-per-category`: 카테고리별 모델 호출 횟수 (기본: `1`)
- `--scenarios-per-response`: 모델 응답 1회당 목표 시나리오 개수 (기본: `5`)
- `--target-per-category`: 카테고리별 최종 목표 시나리오 수 (기본: `responses-per-category * scenarios-per-response`)
- `--categories`: 생성 대상 카테고리 목록
- `--max-examples`: 프롬프트에 넣는 예시 개수 (기본: `5`)
- `--max-disallow`: 프롬프트에 넣는 기존 금지 시나리오 개수 (기본: `5`)

#### 출력 예시

- `[PROGRESS] 쇼핑: existing 3 / target 5`
- `[DONE] 쇼핑: accepted 2 / needed 2 (target 5)`
- `Saved 54 rows to mobile_widget_scenarios.csv`

#### 참고

- 스크립트는 CSV의 `category`+`scenario`를 읽어 카테고리별 누적 개수를 계산하고, `target`까지의 부족분만 생성합니다.
- 모델 출력 품질에 따라 실제 저장 개수는 요청 개수보다 적을 수 있습니다(중복/금지어 필터링).

</details>

### 2단계: Tool Call 생성 🛠️

<details>
<summary><strong>자세히 보기</strong></summary>

`generate_widget_tool_calls.py`는 1단계에서 만든 시나리오 CSV를 읽고, 시나리오별 tool call을 생성해 별도 CSV에 누적 저장합니다.

#### 주요 동작

- 입력: `mobile_widget_scenarios.csv` (기본)
- 시나리오 1개당 최대 3개 tool call 생성(기본)
- tool call 포맷: `function_name(param1=value1, param2=value2, ...)`
- `params` 같은 플레이스홀더 대신 시나리오에 맞는 실제 파라미터/값을 채워 생성
- 기존 tool call 중복이 있어도, 생성 일자/모델/시나리오가 다르면 그대로 추가
- 프롬프트 예시 개수는 `--max-examples`로 조절 가능
- CSV 저장 컬럼
  - `created_at`
  - `model`
  - `scenario_created_at`
  - `scenario_model`
  - `category`
  - `scenario`
  - `prompt`
  - `tool_call`

#### 실행 방법

```bash
python generate_widget_tool_calls.py \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct \
  --scenario-csv mobile_widget_scenarios.csv \
  --tool-call-csv mobile_widget_tool_calls.csv
```

#### 옵션

- `--scenario-csv`: 1단계 시나리오 CSV 경로 (기본: `mobile_widget_scenarios.csv`)
- `--tool-call-csv`: tool call 출력 CSV 경로 (기본: `mobile_widget_tool_calls.csv`)
- `--base-url`: vLLM OpenAI 호환 API URL
- `--api-key`: API 키
- `--model`: 생성 모델명
- `--temperature`: 샘플링 온도 (기본: `0.4`)
- `--max-items-per-scenario`: 시나리오당 최대 tool call 개수 (기본: `3`)
- `--max-examples`: 프롬프트 예시 개수 상한 (기본: `10`)
- `--limit-scenarios`: 앞에서 N개 시나리오만 테스트 생성 (기본: `0`, 전체)

</details>

### 3단계: 구체 예시 JSON 생성 🧪

<details>
<summary><strong>자세히 보기</strong></summary>

`generate_widget_example_json.py`는 1단계 시나리오 + 2단계 tool call을 조합해서, 4단계 JSX/HTML 생성 시 바로 참고할 수 있는 **구체 데이터 JSON** 예시를 생성합니다.

#### 주요 동작

- 입력:
  - `mobile_widget_scenarios.csv` (1단계)
  - `mobile_widget_tool_calls.csv` (2단계)
- 시나리오 1개당 여러 개의 구체 JSON variant 생성 (`--variants-per-scenario`, 기본 3)
- 내장된 10개 JSON 예시 풀에서 시나리오마다 무작위 일부를 선택해 프롬프트에 삽입 (`--max-examples`로 개수 조절)
- 각 JSON 객체에 `tool_calls` 키를 강제 포함
  - tool call이 있으면 함수명만 추출해 `tool_calls`에 반영
  - 필요 없으면 `tool_calls: []`
- 같은 시나리오에서도 다양한 도메인 변형(예: 쇼핑에서 커피/의류/전자제품 등)을 유도
- CSV 저장 컬럼 (단일 파일 누적):
  - `created_at`
  - `model`
  - `scenario_created_at`
  - `scenario_model`
  - `category`
  - `scenario`
  - `prompt`
  - `tool_calls`
  - `variant_index`
  - `difficulty_target` (생성 시 variant index별 목표 난이도)
  - `difficulty` (`low|medium|high:score` 형식, 예: `medium:58`)
  - `example_json`

#### 3단계 난이도(`difficulty`) 산정 기준

`difficulty`는 JSON variant 단위로 계산되며, **모델이 4단계에서 UI를 만들 때의 복잡도**를 근사합니다.
또한 생성 시 기본적으로 같은 시나리오 내 variant들을 **같은 핵심 예시로 유지**한 뒤,
`difficulty_target`을 low→medium→high 순으로 부여해 난이도만 단계적으로 바뀌도록 유도합니다.

기본값은 **랜덤이 아니라 `rotate`(결정적 회전)** 입니다.
- 기본(`--difficulty-strategy rotate`): variant index 기준으로 low→medium→high를 반복
- 랜덤(`--difficulty-strategy random`): `--difficulty-seed` 기반으로 무작위 배치
- 고정(`--difficulty-strategy fixed`): `--difficulty-fixed-level` 하나로 통일

- `tool_calls` 복잡도 (가중치 큼): 고유 tool call 개수가 많을수록 난이도 증가
- 필드 복잡도: `tool_calls` 제외 top-level 필드 수가 많을수록 증가
- 구조 복잡도: 중첩 depth, 객체/배열 노드 수, 배열 원소 수가 많을수록 증가
- payload 복잡도: 문자열 총 길이가 길수록 증가
- 시나리오 복잡도: 시나리오 토큰 수가 많을수록 증가
- tool call 모호성 보정: raw tool call 대비 함수명 추출이 많이 줄어들면 소폭 가산

최종 score(0~100)를 기준으로 다음 레벨을 붙입니다.
- `low`: 0~33
- `medium`: 34~66
- `high`: 67~100

#### 실행 방법

```bash
python generate_widget_example_json.py \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct \
  --scenario-csv mobile_widget_scenarios.csv \
  --tool-call-csv mobile_widget_tool_calls.csv \
  --json-csv mobile_widget_example_json.csv
```

#### 옵션

- `--scenario-csv`: 1단계 시나리오 CSV 경로 (기본: `mobile_widget_scenarios.csv`)
- `--tool-call-csv`: 2단계 tool call CSV 경로 (기본: `mobile_widget_tool_calls.csv`)
- `--json-csv`: 3단계 JSON 예시 CSV 경로 (기본: `mobile_widget_example_json.csv`)
- `--base-url`: vLLM OpenAI 호환 API URL
- `--api-key`: API 키
- `--model`: 생성 모델명
- `--temperature`: 샘플링 온도 (기본: `0.5`)
- `--variants-per-scenario`: 시나리오별 JSON variant 개수 (기본: `3`)
- `--max-examples`: 프롬프트에 넣을 무작위 JSON 예시 개수 (기본: `3`)
- `--example-seed`: 예시 샘플링 시드값 (기본: `42`)
- `--difficulty-strategy`: 난이도 target 배치 방식 `rotate|random|fixed` (기본: `rotate`)
- `--difficulty-fixed-level`: `fixed` 전략일 때 사용할 레벨 `low|medium|high` (기본: `medium`)
- `--difficulty-seed`: `random` 전략 난수 시드 (기본: `42`)
- `--limit-scenarios`: 앞에서 N개 시나리오만 테스트 생성 (기본: `0`, 전체)

</details>

### 4단계: GenUI TSX 생성 🎨

<details>
<summary><strong>자세히 보기</strong></summary>

`generate_genui_tsx.py`는 3단계 JSON(`example_json`)을 입력으로 받아, **component 의존성이 없는 최소 UI 형태의 TSX**를 생성합니다.

#### 주요 동작

- 입력: `mobile_widget_example_json.csv` (3단계)
- 각 JSON row마다 동일한 프롬프트로 여러 번 호출해 샘플 생성 (`--samples-per-input`)
  - 한 번의 호출에서는 TSX 1개만 생성
  - 같은 입력에 대해 여러 번 호출하여 다양한 정답 후보를 축적
- 출력은 SFT 용도로 바로 사용 가능하도록 `prompt` + `example_json` + `tsx_code` 저장
- 기본값으로 `format_ok=1` 및 `uses_declared_tool_calls=1`인 행만 저장 (`--filter-invalid`, 기본 켜짐)
  - 단, `tool_calls`가 빈 입력은 `uses_declared_tool_calls=1`로 간주되어 정상 통과
- CSV 저장 컬럼:
  - `created_at`
  - `model`
  - `json_created_at`
  - `json_model`
  - `scenario_created_at`
  - `scenario_model`
  - `category`
  - `scenario`
  - `json_variant_index`
  - `json_difficulty_target`
  - `json_difficulty`
  - `sample_index`
  - `prompt`
  - `example_json`
  - `tsx_code`
  - `format_ok`
  - `uses_declared_tool_calls`

#### 실행 방법

```bash
python generate_genui_tsx.py \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct \
  --json-csv mobile_widget_example_json.csv \
  --tsx-csv mobile_widget_genui_tsx.csv
```

#### 옵션

- `--json-csv`: 3단계 JSON CSV 경로 (기본: `mobile_widget_example_json.csv`)
- `--tsx-csv`: 4단계 TSX 출력 CSV 경로 (기본: `mobile_widget_genui_tsx.csv`)
- `--base-url`: vLLM OpenAI 호환 API URL
- `--api-key`: API 키
- `--model`: 생성 모델명
- `--temperature`: 샘플링 온도 (기본: `0.3`)
- `--samples-per-input`: 입력 1개당 반복 생성 횟수 (기본: `3`)
- `--max-concurrency`: 동시 요청 워커 수 (기본: `4`)
- `--http-max-connections`: HTTP 총 연결 상한 (기본: `32`)
- `--http-max-keepalive-connections`: keep-alive 연결 상한 (기본: `16`)
- `--limit-rows`: 앞에서 N개 JSON row만 테스트 생성 (기본: `0`, 전체)
- `--filter-invalid` / `--no-filter-invalid`: 출력 전 품질 체크 필터 on/off (기본: on)

#### 메모리/처리량 트레이드오프

- `--max-concurrency`를 크게 올리면 처리량은 증가할 수 있지만, 워커별 응답 객체/문자열이 동시에 메모리에 머물러 **피크 메모리 사용량이 빠르게 증가**합니다.
- 기본값(`4`)은 메모리 안정성을 우선한 보수적 설정입니다. 서버/머신 여유가 충분할 때만 점진적으로 올려 보세요.
- 연결 풀 상한(`--http-max-connections`, `--http-max-keepalive-connections`)을 함께 조정하면 과도한 커넥션 확장을 완화할 수 있습니다.

</details>
