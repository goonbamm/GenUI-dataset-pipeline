# GenUI Dataset Pipeline

Generative UI 모바일 위젯 시나리오 데이터를 카테고리별로 생성하고 CSV로 누적 저장하는 스크립트입니다.

## 1단계: 시나리오 생성

`generate_mobile_widget_scenarios.py`는 vLLM(OpenAI 호환 API)로 카테고리별 시나리오를 생성합니다.

### 주요 동작

- 카테고리별로 시나리오 생성 요청
- 모델 응답 1회당 기본 5개 시나리오 생성
- CSV에 이미 존재하는 카테고리는 자동으로 **생성 제외**
- 예시 목록/기존 시나리오와 중복되지 않도록 프롬프트 제약 + 후처리 필터링
- CSV 저장 컬럼
  - `created_at`
  - `model`
  - `prompt`
  - `category`
  - `scenario`

## 요구 사항

- Python 3.10+
- `openai` 패키지

```bash
pip install openai
```

## 실행 방법

기본 실행:

```bash
python generate_mobile_widget_scenarios.py \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct \
  --csv-path mobile_widget_scenarios.csv
```

환경변수 사용 예시:

```bash
export VLLM_BASE_URL="http://localhost:8000/v1"
export VLLM_MODEL="Qwen/Qwen2.5-7B-Instruct"
export VLLM_API_KEY="EMPTY"
python generate_mobile_widget_scenarios.py
```

카테고리 직접 지정:

```bash
python generate_mobile_widget_scenarios.py --categories 쇼핑 음악 미디어 캘린더 여행 요리 운동
```

## 옵션

- `--csv-path`: 출력 CSV 경로 (기본: `mobile_widget_scenarios.csv`)
- `--base-url`: vLLM OpenAI 호환 API URL (기본: `http://localhost:8000/v1`)
- `--api-key`: API 키 (기본: `VLLM_API_KEY` 또는 `EMPTY`)
- `--model`: 생성 모델명
- `--temperature`: 샘플링 온도 (기본: `0.8`)
- `--responses-per-category`: 카테고리별 모델 호출 횟수 (기본: `1`)
- `--scenarios-per-response`: 모델 응답 1회당 목표 시나리오 개수 (기본: `5`)
- `--categories`: 생성 대상 카테고리 목록
- `--max-examples`: 프롬프트에 넣는 예시 개수 (기본: `5`)
- `--max-disallow`: 프롬프트에 넣는 기존 금지 시나리오 개수 (기본: `5`)

## 출력 예시

실행 시 카테고리별 로그가 출력됩니다.

- `[SKIP] Category already exists in CSV: 쇼핑`
- `[DONE] 여행: accepted 5 / requested 5`
- `Saved 54 rows to mobile_widget_scenarios.csv`

## 참고

- 스크립트는 CSV에 `category` 컬럼이 있을 때 해당 컬럼을 기준으로 중복 카테고리를 판단합니다.
- 모델 출력 품질에 따라 실제 저장 개수는 요청 개수보다 적을 수 있습니다(중복/금지어 필터링).


## 2단계: Action Item 생성

`generate_widget_action_items.py`는 1단계에서 만든 시나리오 CSV를 읽고, 시나리오별 action item을 생성해 별도 CSV에 누적 저장합니다.

### 주요 동작

- 입력: `mobile_widget_scenarios.csv` (기본)
- 시나리오 1개당 최대 3개 action item 생성(기본)
- action item 포맷: `function_name(params) - short description`
- 기존 action item 중복이 있어도, 생성 일자/모델/시나리오가 다르면 그대로 추가
- 프롬프트 예시 개수는 `--max-examples`로 조절 가능
- CSV 저장 컬럼
  - `created_at`
  - `model`
  - `scenario_created_at`
  - `scenario_model`
  - `category`
  - `scenario`
  - `prompt`
  - `action_item`

### 실행 방법

```bash
python generate_widget_action_items.py \
  --base-url http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-7B-Instruct \
  --scenario-csv mobile_widget_scenarios.csv \
  --action-csv mobile_widget_action_items.csv
```

### 옵션

- `--scenario-csv`: 1단계 시나리오 CSV 경로 (기본: `mobile_widget_scenarios.csv`)
- `--action-csv`: action item 출력 CSV 경로 (기본: `mobile_widget_action_items.csv`)
- `--base-url`: vLLM OpenAI 호환 API URL
- `--api-key`: API 키
- `--model`: 생성 모델명
- `--temperature`: 샘플링 온도 (기본: `0.4`)
- `--max-items-per-scenario`: 시나리오당 최대 action item 개수 (기본: `3`)
- `--max-examples`: 프롬프트 예시 개수 상한 (기본: `10`)
- `--limit-scenarios`: 앞에서 N개 시나리오만 테스트 생성 (기본: `0`, 전체)
