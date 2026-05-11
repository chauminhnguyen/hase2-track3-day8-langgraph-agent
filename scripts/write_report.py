"""Helper script to write the comprehensive Vietnamese lab report."""

report = r"""# Báo cáo Lab Ngày 08 — LangGraph Agentic Orchestration

## 1. Thông tin nhóm / sinh viên

- Tên: [Sinh viên điền tên]
- Repo/commit: [Điền commit hash]
- Ngày: 11/05/2026

---

## 2. Kiến trúc hệ thống

### 2.1. Tổng quan kiến trúc

Hệ thống được xây dựng bằng LangGraph — một framework cho phép định nghĩa workflow dạng đồ thị có hướng (directed graph) với các node xử lý và cạnh điều kiện. Mỗi node là một hàm Python thuần nhận `AgentState` vào và trả về partial state update. Các cạnh điều kiện (conditional edges) cho phép định tuyến linh hoạt dựa trên trạng thái hiện tại.

Đồ thị được compile bằng `StateGraph(AgentState).compile(checkpointer=...)` và chạy bằng `graph.invoke(state, config)`. Checkpointer đảm bảo trạng thái được lưu lại giữa các lần gọi, hỗ trợ crash recovery và time travel.

### 2.2. Sơ đồ đồ thị

```
START → intake → classify → [định tuyến điều kiện]
  simple       → answer → finalize → END
  tool         → tool → evaluate → answer → finalize → END
  missing_info → clarify → finalize → END
  risky        → risky_action → approval → tool → evaluate → answer → finalize → END
  error        → retry → tool → evaluate → [vòng lặp retry hoặc answer]
  max retry    → dead_letter → finalize → END
```

Sơ đồ Mermaid chi tiết:

```mermaid
graph TD;
    __start__ --> intake;
    intake --> classify;
    classify -->|simple| answer;
    classify -->|tool| tool;
    classify -->|missing_info| clarify;
    classify -->|risky| risky_action;
    classify -->|error| retry;
    tool --> evaluate;
    evaluate -->|success| answer;
    evaluate -->|needs_retry| retry;
    retry -->|attempt < max| tool;
    retry -->|attempt >= max| dead_letter;
    risky_action --> approval;
    approval -->|approved| tool;
    approval -->|rejected| clarify;
    answer --> finalize;
    clarify --> finalize;
    dead_letter --> finalize;
    finalize --> __end__;
```

### 2.3. Mô tả chi tiết từng node

| Node | File nguồn | Chức năng | Đầu vào chính | Đầu ra chính |
|---|---|---|---|---|
| **intake** | `nodes.py:12-29` | Chuẩn hóa query, phát hiện PII (email/phone), tạo metadata | `query` thô | `query` đã chuẩn hóa, `messages`, `events` |
| **classify** | `nodes.py:43-80` | Phân loại query thành 1 trong 5 route dựa trên từ khóa với thứ tự ưu tiên | `query` | `route`, `risk_level`, `events` |
| **tool** | `nodes.py:101-115` | Gọi mock tool; mô phỏng lỗi tạm thời cho error-route (khi attempt < 2) | `attempt`, `route`, `scenario_id` | `tool_results`, `events` |
| **evaluate** | `nodes.py:209-224` | Kiểm tra kết quả tool — phát hiện "ERROR" trong tool_result để quyết định retry | `tool_results` | `evaluation_result` ("success"/"needs_retry"), `events` |
| **risky_action** | `nodes.py:118-140` | Chuẩn bị mô tả hành động rủi ro cụ thể (refund/delete/cancel/send) | `query` | `proposed_action`, `events` |
| **approval** | `nodes.py:143-169` | Phê duyệt HITL — hỗ trợ `interrupt()` thật khi `LANGGRAPH_INTERRUPT=true`, mặc định mock approved | `proposed_action`, `risk_level` | `approval` dict, `events` |
| **retry** | `nodes.py:172-183` | Tăng attempt counter, ghi nhận lỗi tạm thời | `attempt` | `attempt` +1, `errors`, `events` |
| **dead_letter** | `nodes.py:227-236` | Ghi log thất bại không thể phục hồi khi vượt quá max_attempts | `attempt` | `final_answer`, `events` |
| **answer** | `nodes.py:186-206` | Tạo câu trả lời cuối cùng — dựa trên tool_results và approval context | `tool_results`, `approval`, `route` | `final_answer`, `events` |
| **clarify** | `nodes.py:83-98` | Yêu cầu thông tin bổ sung khi query quá ngắn hoặc mơ hồ | `query` | `pending_question`, `final_answer`, `events` |
| **finalize** | `nodes.py:239-241` | Kết thúc workflow, emit audit event cuối cùng | — | `events` |

### 2.4. Chi tiết các hàm định tuyến (routing.py)

Hệ thống có 4 hàm định tuyến điều kiện, mỗi hàm đọc state hiện tại và trả về tên node tiếp theo:

**`route_after_classify`** (`routing.py:8-21`):
- Ánh xạ `route` string → tên node tiếp theo
- Mapping: `simple→answer`, `tool→tool`, `missing_info→clarify`, `risky→risky_action`, `error→retry`
- Nếu route không nhận diện được → mặc định `"answer"` (fallback an toàn)

**`route_after_evaluate`** (`routing.py:34-42`):
- Kiểm tra `evaluation_result`: nếu `"needs_retry"` → `"retry"`, ngược lại → `"answer"`
- Đây là "done?" check — lợi thế chính của LangGraph so với LCEL thông thường

**`route_after_retry`** (`routing.py:24-31`):
- Kiểm tra `attempt >= max_attempts`: nếu đúng → `"dead_letter"`, ngược lại → `"tool"` để thử lại
- Đây là lớp bảo vệ chống vòng lặp vô hạn — quan trọng nhất cho error scenarios

**`route_after_approval`** (`routing.py:45-51`):
- Kiểm tra `approval.approved`: nếu True → `"tool"` (thực hiện hành động), nếu False → `"clarify"` (yêu cầu thêm thông tin)

### 2.5. Thuật toán phân loại (classify_node)

Phân loại sử dụng heuristic dựa trên từ khóa với thứ tự ưu tiên rõ ràng. Hàm `_word_match()` sử dụng regex `\b{keyword}\b` để khớp từ nguyên vẹn, tránh lỗi "it" match trong "item".

```
Ưu tiên 1 — RISKY:    refund, delete, send, cancel, remove, revoke
Ưu tiên 2 — TOOL:     status, order, lookup, check, track, find, search
Ưu tiên 3 — MISSING:  query < 5 từ + đại từ (it, that, this, thing)
Ưu tiên 4 — ERROR:    timeout, fail, failure, error, crash, unavailable
Ưu tiên 5 — SIMPLE:   mặc định (không match bất kỳ từ khóa nào trên)
```

**Tại sao thứ tự ưu tiên quan trọng?** Một query như "Cancel my order" chứa cả "cancel" (risky) và "order" (tool). Nếu kiểm tra tool trước, hệ thống sẽ chỉ tra cứu đơn hàng thay vì yêu cầu phê duyệt hủy — gây rủi ro. Ưu tiên risky trước đảm bảo hành động rủi ro luôn được bắt.

**Tại sao cần word boundary?** Query "Can you fix it?" cần match "it" như một từ độc lập. Không có `\b`, từ "it" sẽ bị match bên trong "item", "iteration", "commit" — gây phân loại sai.

---

## 3. State Schema

### 3.1. Bảng các trường state

`AgentState` là `TypedDict` với `total=False` — mọi trường đều tùy chọn. Điều này tương thích với cách LangGraph merge partial state updates.

| Trường | Kiểu | Reducer | Giá trị mặc định | Lý do chọn reducer |
|---|---|---|---|---|
| `thread_id` | `str` | overwrite | `"thread-{id}"` | ID duy nhất cho mỗi chạy, chỉ cần giá trị cuối |
| `scenario_id` | `str` | overwrite | từ Scenario | ID kịch bản, không thay đổi trong quá trình chạy |
| `query` | `str` | overwrite | từ Scenario | Query hiện tại (đã chuẩn hóa bởi intake), ghi đè khi PII redacted |
| `route` | `str` | overwrite | `""` | Route hiện tại do classify gán, chỉ giữ 1 giá trị |
| `risk_level` | `str` | overwrite | `"unknown"` | Mức rủi ro: low/high, chỉ cần giá trị cuối |
| `attempt` | `int` | overwrite | `0` | Số lần thử, retry_node tăng +1 mỗi lần |
| `max_attempts` | `int` | overwrite | từ Scenario (mặc định 3) | Giới hạn retry, không thay đổi |
| `final_answer` | `str\|None` | overwrite | `None` | Câu trả lời cuối cùng, chỉ cần 1 giá trị |
| `pending_question` | `str\|None` | overwrite | `None` | Câu hỏi clarification cho người dùng |
| `proposed_action` | `str\|None` | overwrite | `None` | Mô tả hành động rủi ro cần phê duyệt |
| `approval` | `dict\|None` | overwrite | `None` | Kết quả phê duyệt (approved, reviewer, comment) |
| `evaluation_result` | `str\|None` | overwrite | `None` | Kết quả đánh giá: "success" hoặc "needs_retry" |
| `messages` | `Annotated[list[str], add]` | **append** | `[]` | Lịch sử tin nhắn — tích lũy, không ghi đè để audit |
| `tool_results` | `Annotated[list[str], add]` | **append** | `[]` | Kết quả tool — tích lũy qua các lần retry để debug |
| `errors` | `Annotated[list[str], add]` | **append** | `[]` | Lỗi tích lũy — mỗi retry ghi thêm, không mất lỗi cũ |
| `events` | `Annotated[list[dict], add]` | **append** | `[]` | Audit trail — mỗi node ghi thêm event, tạo lịch sử hoàn chỉnh |

### 3.2. Nguyên tắc thiết kế state

- **Overwrite** cho các trường chỉ cần giá trị cuối cùng (route, attempt, final_answer). Giảm kích thước state, tránh tích lũy dữ liệu thừa.
- **Append (add reducer)** cho các trường cần lịch sử (events, errors, tool_results, messages). Đảm bảo audit trail hoàn chỉnh — có thể xem lại toàn bộ quá trình xử lý.
- **Lean & serializable**: Mọi trường đều là kiểu cơ bản (str, int, list[str], dict) — không có object phức tạp, đảm bảo JSON serialization cho checkpoint và metrics.

### 3.3. Mô hình dữ liệu hỗ trợ

- **`Route(StrEnum)`**: Enum định nghĩa 7 giá trị route: `simple`, `tool`, `missing_info`, `risky`, `error`, `dead_letter`, `done`
- **`LabEvent(BaseModel)`**: Audit event chuẩn hóa với `node`, `event_type`, `message`, `latency_ms`, `metadata` — Pydantic đảm bảo validation
- **`ApprovalDecision(BaseModel)`**: Kết quả phê duyệt với `approved`, `reviewer`, `comment`
- **`Scenario(BaseModel)`**: Kịch bản kiểm thử với validator `query_must_not_be_empty`

---

## 4. Kết quả các kịch bản (Metrics Analysis)

### 4.1. Bảng kết quả chi tiết

Từ `outputs/metrics.json` (chạy bằng `make run-scenarios`):

| Kịch bản | Route kỳ vọng | Route thực tế | Thành công | Nodes đã thăm | Số lần retry | Số lần interrupt | Approval yêu cầu | Approval quan sát | Lỗi |
|---|---|---|---|---:|---:|---:|---|---|---|
| S01_simple | simple | simple | ✅ | 4 | 0 | 0 | Không | Không | — |
| S02_tool | tool | tool | ✅ | 6 | 0 | 0 | Không | Không | — |
| S03_missing | missing_info | missing_info | ✅ | 4 | 0 | 0 | Không | Không | — |
| S04_risky | risky | risky | ✅ | 8 | 0 | 1 | Có | Có | — |
| S05_error | error | error | ✅ | 10 | 2 | 0 | Không | Không | transient failure attempt=1, attempt=2 |
| S06_delete | risky | risky | ✅ | 8 | 0 | 1 | Có | Có | — |
| S07_dead_letter | error | error | ✅ | 5 | 1 | 0 | Không | Không | transient failure attempt=1 |

### 4.2. Chỉ số tổng hợp

| Chỉ số | Giá trị |
|---|---|
| Tổng số kịch bản | 7 |
| **Tỷ lệ thành công** | **100.00%** (7/7) |
| Trung bình nodes đã thăm | 6.43 |
| Tổng số lần retry | 3 |
| Tổng số lần interrupt (HITL) | 2 |
| Resume success | Chưa test (cần SQLite) |

### 4.3. Phân tích chi tiết từng kịch bản

**S01_simple** — "How do I reset my password?"
- Route: simple → answer → finalize. Không chứa từ khóa risky/tool/error → mặc định simple.
- 4 nodes: intake → classify → answer → finalize. Đường đi ngắn nhất.

**S02_tool** — "Please lookup order status for order 12345"
- Route: tool → tool → evaluate → answer → finalize. Chứa "lookup" và "order" (từ khóa tool).
- 6 nodes: intake → classify → tool → evaluate → answer → finalize. Evaluate trả về "success" vì mock tool không lỗi.

**S03_missing** — "Can you fix it?"
- Route: missing_info → clarify → finalize. Query chỉ 4 từ + chứa đại từ "it" → missing_info.
- 4 nodes: intake → classify → clarify → finalize. Yêu cầu thêm thông tin thay vì đoán mò.

**S04_risky** — "Refund this customer and send confirmation email"
- Route: risky → risky_action → approval → tool → evaluate → answer → finalize. Chứa "refund" và "send" (từ khóa risky, ưu tiên 1).
- 8 nodes: đường đi dài nhất vì cần phê duyệt trước khi thực hiện.
- Interrupt count = 1: approval_node đã kích hoạt (mock approved=True).
- `requires_approval=true` → metric kiểm tra cả `approval_observed=true` → thành công.

**S05_error** — "Timeout failure while processing request"
- Route: error → retry → tool → evaluate → [retry loop] → tool → evaluate → answer → finalize.
- Chứa "timeout" và "failure" (từ khóa error, ưu tiên 4).
- 10 nodes: nhiều nhất vì có 2 lần retry. Tool_node mô phỏng lỗi tạm thời khi `attempt < 2`:
  - Lần 1 (attempt=0): tool trả về "ERROR: transient failure" → evaluate → needs_retry → retry (attempt=1)
  - Lần 2 (attempt=1): tool trả về "ERROR: transient failure" → evaluate → needs_retry → retry (attempt=2)
  - Lần 3 (attempt=2): tool trả về "mock-tool-result" (attempt >= 2 nên thành công) → evaluate → success → answer

**S06_delete** — "Delete customer account after support verification"
- Route: risky → risky_action → approval → tool → evaluate → answer → finalize. Chứa "delete" (từ khóa risky).
- 8 nodes: tương tự S04_risky. Interrupt count = 1 cho approval.
- risky_action_node tạo mô tả cụ thể: "Delete customer account — irreversible data loss, requires approval".

**S07_dead_letter** — "System failure cannot recover after multiple attempts"
- Route: error → retry → tool → evaluate → [dead_letter]. max_attempts=1 nên chỉ thử 1 lần.
- 5 nodes: intake → classify → retry → tool → evaluate → retry → dead_letter → finalize.
- Lần 1 (attempt=0): retry tăng attempt=1, tool trả về ERROR → evaluate → needs_retry → retry → attempt(1) >= max_attempts(1) → dead_letter.
- Đây là kịch bản kiểm tra lớp bảo vệ dead_letter — khi max_attempts thấp, hệ thống phải dừng đúng lúc.

### 4.4. Phân tích xu hướng

- **Đường đi ngắn** (4 nodes): simple, missing_info — không cần tool hay approval, xử lý nhanh.
- **Đường đi trung bình** (6 nodes): tool — cần gọi tool + evaluate nhưng không retry.
- **Đường đi dài** (8 nodes): risky — cần approval + tool + evaluate.
- **Đường đi dài nhất** (10 nodes): error với retry — vòng lặp retry làm tăng số node đáng kể.

**Hệ quả production**: Các kịch bản error tốn nhiều tài nguyên hơn (nhiều node, nhiều LLM call nếu dùng LLM). Cần giới hạn max_attempts thấp (3) và monitor tỷ lệ dead_letter.

---

## 5. Phân tích thất bại (Failure Analysis)

### 5.1. Vòng lặp retry không giới hạn (Unbounded Retry Loop)

**Mô tả**: Nếu `route_after_retry()` không kiểm tra `attempt >= max_attempts`, đồ thị sẽ lặp vô hạn giữa retry → tool → evaluate → retry khi tool liên tục thất bại.

**Kịch bản liên quan**: S05_error (max_attempts=3, retry 2 lần rồi thành công) và S07_dead_letter (max_attempts=1, dead_letter ngay).

**Cách phòng tránh**: `route_after_retry()` so sánh `attempt` với `max_attempts`. Khi vượt quá, chuyển sang `dead_letter` node — lớp bảo vệ cuối cùng ghi log cho manual review.

**Rủi ro nếu không có**: Process treo indefinitely, tốn tài nguyên, không bao giờ trả kết quả cho user.

### 5.2. Hành động rủi ro không được phê duyệt (Risky Action Without Approval)

**Mô tả**: Nếu route risky bỏ qua approval_node (ví dụ: classify → risky_action → tool trực tiếp), các hành động phá hủy như refund, delete sẽ thực hiện tự động không cần xác nhận.

**Kịch bản liên quan**: S04_risky (refund + send email) và S06_delete (xóa tài khoản) — cả hai đều `requires_approval=true`.

**Cách phòng tránh**: Route risky bắt buộc đi qua risky_action → approval → tool. Approval node hỗ trợ:
- Mock approval (mặc định): `approved=True` cho testing/CI
- Real HITL: `LANGGRAPH_INTERRUPT=true` → gọi `interrupt()` để tạm dừng graph, chờ reviewer thực sự

**Rủi ro nếu không có**: Xóa dữ liệu khách hàng vô tình, hoàn tiền sai, gửi email nhạy cảm không qua duyệt.

### 5.3. Xung đột từ khóa (Keyword Conflicts)

**Mô tả**: Query chứa từ khóa thuộc nhiều route. Ví dụ: "Cancel my order" chứa "cancel" (risky) và "order" (tool). Nếu kiểm tra tool trước, hệ thống chỉ tra cứu đơn hàng thay vì yêu cầu phê duyệt hủy.

**Cách phòng tránh**: Thứ tự ưu tiên strict — kiểm tra risky trước, rồi tool, rồi missing_info, rồi error, rồi simple. Đảm bảo hành động rủi ro luôn được ưu tiên phát hiện.

**Ví dụ thực tế**: "Refund order status" → "refund" match risky (ưu tiên 1) → route=risky, không phải tool. Đúng vì refund là hành động tài chính cần phê duyệt.

### 5.4. Khớp từ khóa con sai (Substring Matching)

**Mô tả**: Từ "it" trong "Can you fix it?" phải match như từ độc lập, không phải bên trong "item", "iteration", "commit", "audit". Tương tự, "fail" không nên match trong "failure" (cần cả hai từ khóa riêng).

**Cách phòng tránh**: Hàm `_word_match()` sử dụng `re.search(rf"\b{re.escape(keyword)}\b", text)` — `\b` đảm bảo word boundary. Đồng thời thêm cả "fail" và "failure" vào danh sách từ khóa error để bao phủ cả hai dạng.

### 5.5. Quên finalize node (Missing Finalize)

**Mô tả**: Mọi route phải kết thúc bằng finalize → END. Nếu quên thêm cạnh `dead_letter → finalize`, đồ thị sẽ không bao giờ kết thúc cho kịch bản dead_letter.

**Cách phòng tránh**: Trong `graph.py`, mọi đường dẫn cuối cùng đều nối vào `finalize` trước khi đến `END`:
- `answer → finalize → END`
- `clarify → finalize → END`
- `dead_letter → finalize → END`

---

## 6. Bằng chứng về Persistence / Recovery

### 6.1. Kiến trúc Checkpointer

Hệ thống hỗ trợ 3 loại checkpointer qua `build_checkpointer(kind, database_url)`:

| Loại | Class | Use case | Đặc điểm |
|---|---|---|---|
| **memory** | `MemorySaver()` | Development, testing | Nhanh, không cần infrastructure, mất dữ liệu khi process kết thúc |
| **sqlite** | `SqliteSaver(conn=...)` | Persistence, crash recovery | Dữ liệu tồn tại qua process restart, WAL mode cho hiệu năng |
| **postgres** | `PostgresSaver.from_conn_string()` | Production | ACID, replication, high availability |

### 6.2. Cách checkpointer được sử dụng

```python
# cli.py — mỗi scenario chạy với thread_id riêng
for scenario in scenarios:
    state = initial_state(scenario)
    run_config = {"configurable": {"thread_id": state["thread_id"]}}
    final_state = graph.invoke(state, config=run_config)
```

- Thread ID format: `thread-{scenario_id}` (ví dụ: `thread-S01_simple`)
- Checkpointer tự động lưu state sau mỗi node execution
- Có thể truy xuất state history bằng `graph.get_state_history(config)`

### 6.3. SQLite — cải tiến đã thực hiện

Đã nâng cấp `persistence.py` từ API cũ `SqliteSaver.from_conn_string()` sang `SqliteSaver(conn=sqlite3.connect(...))` theo khuyến nghị của langgraph-checkpoint-sqlite 3.x. Bật WAL mode (`PRAGMA journal_mode=WAL`) để:
- Cho phép đọc ghi đồng thời (concurrent reads during writes)
- Giảm nguy cơ database corruption khi crash
- Hiệu năng tốt hơn so với journal mode mặc định

### 6.4. Config

```yaml
# configs/lab.yaml
scenarios_path: data/sample/scenarios.jsonl
checkpointer: memory       # Đổi thành "sqlite" để test persistence
report_path: reports/lab_report.md
```

---

## 7. Công việc mở rộng (Extension Work)

### 7.1. Mermaid Graph Diagram

Xuất đồ thị sang định dạng Mermaid bằng `graph.get_graph().draw_mermaid()`. Sơ đồ trực quan hóa toàn bộ workflow — hữu ích cho documentation và onboarding.

### 7.2. PII Detection trong Intake Node

Intake node tự động phát hiện và che dấu thông tin cá nhân trước khi xử lý:
- **Email**: regex `[\w.+-]+@[\w-]+\.[\w.-]+` → thay bằng `[EMAIL]`
- **Số điện thoại**: regex `\d{3}[-.]?\d{3}[-.]?\d{4}` → thay bằng `[PHONE]`

Event ghi nhận `pii_detected=True/False` để audit. Điều này đảm bảo dữ liệu nhạy cảm không lan truyền qua các node khác.

### 7.3. SQLite Checkpointer nâng cấp

Sử dụng `SqliteSaver(conn=...)` với WAL mode (chi tiết ở mục 6.3). Cho phép crash recovery — nếu process bị kill giữa chừng, checkpoint SQLite vẫn tồn tại và có thể resume.

---

## 8. Kế hoạch cải tiến (Improvement Plan)

Nếu có thêm thời gian để productionize, sẽ ưu tiên theo thứ tự:

### 8.1. LLM-as-judge cho evaluate_node (Ưu tiên cao)

**Vấn đề hiện tại**: `evaluate_node` chỉ kiểm tra chuỗi "ERROR" trong tool_result — heuristic đơn giản, dễ bị bypass (tool trả về lỗi không chứa "ERROR", hoặc "ERROR" xuất hiện trong dữ liệu hợp lệ).

**Cải tiến**: Thay bằng LLM đánh giá chất lượng kết quả:
```python
prompt = f"Rate this tool result: {tool_result}. Is it satisfactory for query: {query}?"
evaluation = llm.invoke(prompt)  # → "success" hoặc "needs_retry"
```

**Lợi ích**: Phát hiện lỗi tính tình (sai ngữ nghĩa, thiếu thông tin) không chỉ lỗi kỹ thuật. Linh hoạt hơn với các loại tool khác nhau.

### 8.2. Real HITL với Streamlit UI (Ưu tiên cao)

**Vấn đề hiện tại**: Approval node chỉ dùng mock approval (`approved=True`) trong dev. `interrupt()` đã được hỗ trợ nhưng chưa có UI.

**Cải tiến**: Xây dựng giao diện Streamlit:
- Hiển thị proposed_action và risk_level
- Nút Approve/Reject với comment
- Gọi `graph.update_state(config, {"approval": decision})` + `graph.invoke(None, config)` để resume

**Lợi ích**: Reviewer thực sự có thể duyệt/từ chối hành động rủi ro, phù hợp yêu cầu compliance.

### 8.3. Parallel Fan-out/Fan-in (Ưu tiên trung bình)

**Vấn đề hiện tại**: Tool node chỉ gọi 1 tool duy nhất. Nhiều trường hợp cần gọi nhiều tool song song (ví dụ: kiểm tra order status + customer history + inventory).

**Cải tiến**: Sử dụng `Send()` API của LangGraph:
```python
def tool_fan_out(state):
    return [Send("tool", {"query": q}) for q in sub_queries]
```
Kết quả gộp tự động nhờ `add` reducer trên `tool_results`.

**Lợi ích**: Giảm latency tổng thể khi cần nhiều tool call độc lập.

### 8.4. Crash Recovery minh chứng với SQLite (Ưu tiên trung bình)

**Vấn đề hiện tại**: Chưa có minh chứng thực tế rằng checkpoint tồn tại qua process kill + restart.

**Cải tiến**: Viết test:
1. Chạy graph với SQLite checkpointer → ghi checkpoint
2. Kill process (simulated crash)
3. Khởi động lại → `graph.invoke(None, config)` với cùng thread_id
4. Verify state được phục hồi từ checkpoint cuối cùng

**Lợi ích**: Đảm bảo hệ thống chịu được failure trong production.

### 8.5. Exponential Backoff cho Retry (Ưu tiên thấp)

**Vấn đề hiện tại**: Retry node thử lại ngay lập tức — không có khoảng nghỉ giữa các lần thử.

**Cải tiến**: Thêm delay tăng dần (1s, 2s, 4s, 8s):
```python
import time
backoff = 2 ** attempt  # 1, 2, 4, 8...
time.sleep(backoff)
```

**Lợi ích**: Giảm tải hệ thống khi có lỗi dai dẳng, tránh "thundering herd" khi nhiều request retry đồng thời.

### 8.6. Monitoring và Alerting (Ưu tiên thấp)

**Cải tiến**: Tích hợp LangSmith tracing:
- Theo dõi latency mỗi node
- Track error rate và dead_letter rate
- Gửi cảnh báo (Slack/PagerDuty) khi dead_letter tăng đột ngột
- Dashboard hiển thị phân bố route (bao nhiêu % simple/tool/risky/error)

**Lợi ích**: Visibility vào hệ thống production, phát hiện vấn đề sớm.

### 8.7. LLM-based Classification thay vì Keyword Heuristic (Ưu tiên thấp, dài hạn)

**Vấn đề hiện tại**: Keyword heuristic hoạt động tốt cho 7 kịch bản mẫu nhưng có thể miss các query không chứa từ khóa chính xác.

**Cải tiến**: Dùng LLM classifier:
```python
prompt = f"Classify this support query into one of: simple, tool, missing_info, risky, error. Query: {query}"
route = llm.invoke(prompt)
```

**Lợi ích**: Xử lý được query mơ hồ, synonym, và ngôn ngữ tự nhiên phức tạp hơn.
**Rủi ro**: Thêm latency, chi phí API, cần fallback về keyword khi LLM không khả dụng.
"""

with open("reports/lab_report.md", "w", encoding="utf-8") as f:
    f.write(report)

print("Report written successfully to reports/lab_report.md")
