"""Helper script to write the Vietnamese lab report."""

report = r"""# Báo cáo Lab Ngày 08 — LangGraph Agentic Orchestration

## 1. Thông tin nhóm / sinh viên

- Tên: [Sinh viên điền tên]
- Repo/commit: [Điền commit hash]
- Ngày: 11/05/2026

## 2. Kiến trúc hệ thống

Đồ thị LangGraph được xây dựng theo kiến trúc đường ống (pipeline) với các node chính:

```
START → intake → classify → [định tuyến điều kiện]
  simple       → answer → finalize → END
  tool         → tool → evaluate → answer → finalize → END
  missing_info → clarify → finalize → END
  risky        → risky_action → approval → tool → evaluate → answer → finalize → END
  error        → retry → tool → evaluate → [vòng lặp retry hoặc answer]
  max retry    → dead_letter → finalize → END
```

### Các node chính:

- **intake**: Chuẩn hóa query, kiểm tra PII (email, số điện thoại), tạo metadata
- **classify**: Phân loại query dựa trên từ khóa với thứ tự ưu tiên: risky > tool > missing_info > error > simple
- **tool**: Gọi mock tool, mô phỏng lỗi tạm thời cho error-route
- **evaluate**: Kiểm tra kết quả tool — nếu có ERROR thì cần retry
- **risky_action**: Chuẩn bị hành động rủi ro với lý do cụ thể
- **approval**: Bước phê duyệt HITL — hỗ trợ interrupt() thật khi LANGGRAPH_INTERRUPT=true
- **retry**: Ghi nhận lần thử, kiểm tra giới hạn max_attempts
- **dead_letter**: Ghi log thất bại không thể phục hồi sau khi vượt quá số lần thử
- **answer**: Tạo câu trả lời cuối cùng dựa trên tool_results và approval
- **clarify**: Yêu cầu thông tin bổ sung khi query quá ngắn/từ mờ
- **finalize**: Kết thúc workflow và emit audit event cuối cùng

### Cách định tuyến hoạt động:

1. **route_after_classify**: Ánh xạ route string → tên node tiếp theo
2. **route_after_evaluate**: Nếu needs_retry → retry, ngược lại → answer
3. **route_after_retry**: Nếu attempt >= max_attempts → dead_letter, ngược lại → tool
4. **route_after_approval**: Nếu approved → tool, ngược lại → clarify

## 3. State Schema

| Trường | Reducer | Lý do |
|---|---|---|
| thread_id | overwrite | ID duy nhất cho mỗi chạy |
| scenario_id | overwrite | ID của kịch bản |
| query | overwrite | Query hiện tại (đã chuẩn hóa) |
| route | overwrite | Route hiện tại, chỉ giữ 1 giá trị |
| risk_level | overwrite | Mức độ rủi ro: low/medium/high |
| attempt | overwrite | Số lần thử hiện tại |
| max_attempts | overwrite | Giới hạn số lần thử |
| final_answer | overwrite | Câu trả lời cuối cùng |
| pending_question | overwrite | Câu hỏi cho người dùng |
| proposed_action | overwrite | Hành động để duyệt |
| approval | overwrite | Kết quả phê duyệt |
| evaluation_result | overwrite | Kết quả đánh giá: success/needs_retry |
| messages | append (add) | Lịch sử tin nhắn, không ghi đè |
| tool_results | append (add) | Kết quả tool tích lũy |
| errors | append (add) | Lỗi tích lũy cho debug |
| events | append (add) | Audit trail, mỗi node ghi thêm event |

### Từ khóa phân loại với thứ tự ưu tiên:

| Route | Từ khóa | Ưu tiên |
|---|---|---|
| risky | refund, delete, send, cancel, remove, revoke | 1 (cao nhất) |
| tool | status, order, lookup, check, track, find, search | 2 |
| missing_info | Query < 5 từ + đại từ (it, that, this, thing) | 3 |
| error | timeout, fail, failure, error, crash, unavailable | 4 |
| simple | Mặc định — không match bất kỳ route nào trên | 5 (thấp nhất) |

## 4. Kết quả các kịch bản

Từ outputs/metrics.json:

| Kịch bản | Route kỳ vọng | Route thực tế | Thành công | Số lần retry | Số lần interrupt |
|---|---|---|---|---:|---:|
| S01_simple | simple | simple | ✅ | 0 | 0 |
| S02_tool | tool | tool | ✅ | 0 | 0 |
| S03_missing | missing_info | missing_info | ✅ | 0 | 0 |
| S04_risky | risky | risky | ✅ | 0 | 1 |
| S05_error | error | error | ✅ | 2 | 0 |
| S06_delete | risky | risky | ✅ | 0 | 1 |
| S07_dead_letter | error | error | ✅ | 1 | 0 |

**Tỷ lệ thành công: 100% (7/7)**
**Trung bình nodes đã thăm: 6.43**
**Tổng số lần retry: 3**
**Tổng số lần interrupt: 2**

## 5. Phân tích thất bại

### 5.1. Vòng lặp retry không giới hạn (Unbounded retry)

Nếu không kiểm tra `attempt < max_attempts`, các kịch bản error sẽ lặp mãi không dừng. Ví dụ S07_dead_letter có max_attempts=1, nên chỉ thử 1 lần rồi chuyển sang dead_letter ngay. Đây là lớp bảo vệ quan trọng nhất của hệ thống.

**Giải pháp**: Luôn kiểm tra giới hạn trong `route_after_retry()`. Khi attempt >= max_attempts, chuyển sang dead_letter thay vì tiếp tục retry.

### 5.2. Hành động rủi ro không được phê duyệt (Risky action without approval)

Nếu bỏ qua approval_node, các hành động như refund hay delete có thể được thực hiện tự động, gây mất dữ liệu hoặc ảnh hưởng tài chính. Ví dụ S04_risky (refund + send email) và S06_delete (xóa tài khoản) đều yêu cầu phê duyệt trước khi thực hiện.

**Giải pháp**: Route risky luôn đi qua risky_action → approval trước khi đến tool. Approval node hỗ trợ HITL thực sự với `interrupt()` khi đặt biến môi trường.

### 5.3. Xung đột từ khóa (Keyword conflicts)

Query "Check order status" chứa cả "check" (tool) và "order" (tool), nhưng cũng có thể có từ khóa risky như "cancel order". Thứ tự ưu tiên là rất quan trọng: risky > tool > missing_info > error > simple. Điều này đảm bảo rằng hành động rủi ro luôn được bắt trước.

### 5.4. Khớp từ khóa sai (Word boundary matching)

Từ "it" trong "Can you fix it?" phải được match như một từ độc lập, không phải như phần của "item" hoặc "iteration". Sử dụng regex `\b{keyword}\b` để đảm bảo word boundary chính xác.

## 6. Bằng chứng về Persistence / Recovery

### Checkpointer được tích hợp:

- **Memory (dev)**: Sử dụng `MemorySaver()` mặc định, phù hợp cho development và testing
- **SQLite**: Sử dụng `SqliteSaver(conn=sqlite3.connect(...))` với WAL mode, đảm bảo dữ liệu tồn tại qua các lần chạy
- **Postgres**: Hỗ trợ production với `PostgresSaver.from_conn_string()`

### Cách sử dụng:

```python
# Mỗi scenario chạy với thread_id riêng
run_config = {"configurable": {"thread_id": state["thread_id"]}}
final_state = graph.invoke(state, config=run_config)
```

- Thread ID duy nhất cho mỗi scenario (ví dụ: `thread-S01_simple`)
- MemorySaver cho phép kiểm tra state history qua `get_state_history()`
- SQLite WAL mode đảm bảo hiệu năng ghi đọc đồng thời

## 7. Công việc mở rộng (Extension)

### 7.1. Mermaid Graph Diagram

Đồ thị được xuất ra định dạng Mermaid:

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

### 7.2. PII Detection trong Intake

Intake node tự động phát hiện và che dấu thông tin cá nhân:
- Email: `[\w.+-]+@[\w-]+\.[\w.-]+` → `[EMAIL]`
- Số điện thoại: `\d{3}[-.]?\d{3}[-.]?\d{4}` → `[PHONE]`

### 7.3. SQLite Checkpointer

Đã nâng cấp persistence.py để sử dụng API `SqliteSaver(conn=...)` thay vì `from_conn_string()` cũ (tương thích với langgraph-checkpoint-sqlite 3.x). WAL mode được bật để tối ưu hiệu năng.

## 8. Kế hoạch cải tiến

Nếu có thêm 1 ngày để productionize, sẽ ưu tiên:

1. **LLM-as-judge cho evaluate_node**: Thay thế heuristic kiểm tra "ERROR" trong tool result bằng LLM đánh giá chất lượng câu trả lời, giúp phát hiện lỗi tính tình không phải chỉ lỗi kỹ thuật.

2. **Real HITL với Streamlit UI**: Xây dựng giao diện phê duyệt/từ chối thực sự với interrupt()/resume(), cho phép reviewer thật tương tác với graph.

3. **Parallel fan-out**: Sử dụng `Send()` để chạy nhiều tool đồng thời (ví dụ: kiểm tra order status + kiểm tra customer history), gộp kết quả bằng add reducer.

4. **Crash recovery với SQLite**: Minh chứng rằng checkpoint tồn tại qua process kill + restart, đảm bảo không mất trạng thái khi hệ thống thất bại.

5. **Monitoring và alerting**: Tích hợp LangSmith tracing để theo dõi latency, error rate, và gửi cảnh báo khi dead_letter tăng đột ngột.

6. **Exponential backoff**: Thay đổi retry_node để tăng khoảng thời gian giữa các lần thử (1s, 2s, 4s...) thay vì thử ngay lập tức, giảm tải hệ thống khi có lỗi dai dẳng.
"""

with open("reports/lab_report.md", "w", encoding="utf-8") as f:
    f.write(report)

print("Report written successfully to reports/lab_report.md")
