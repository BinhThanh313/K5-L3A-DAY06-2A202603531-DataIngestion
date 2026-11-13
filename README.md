# Day 06 · Data Ingestion Engineering — Báo cáo Thực nghiệm

## 1. Tóm tắt dự án & Kết quả thực nghiệm

Dự án hiện thực trọn vẹn pipeline nạp dữ liệu (Data Ingestion Pipeline) từ tài liệu báo cáo kinh tế của World Bank sang cơ sở dữ liệu vector (PostgreSQL 16 + pgvector), đánh giá và so sánh thực nghiệm **3 chiến lược chuẩn bị dữ liệu (A / B / C)** trên bộ benchmark chuẩn gồm **20 câu hỏi (15 dev / 5 test)**.

### Bảng kết quả benchmark tổng hợp:

| Chiến lược | Cơ chế Chunking & Filter | Dev Hit@1 | Dev Hit@3 | Test Hit@1 | Test Hit@3 | All Hit@3 | Coherence (TB/2) | Grounding (TB/2) | Filter đúng | Chi phí |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Strategy A** (Baseline) | Fixed-size 500 ký tự, overlap 50, không filter | 53.33% (8/15) | 73.33% (11/15) | 60.00% (3/5) | 60.00% (3/5) | 70.00% (14/20) | 0.90 / 2.0 | 1.60 / 2.0 | N/A | $0.00130 |
| **Strategy B** | Structure-aware Paragraph 500 ký tự, không filter | 66.67% (10/15) | 80.00% (12/15) | 60.00% (3/5) | **100.0%** (5/5) | 85.00% (17/20) | **1.60 / 2.0** | **2.00 / 2.0** | N/A | $0.00135 |
| **Strategy C** (Cốt lõi) | Paragraph 500 ký tự + **SQL Filter trên `section`** | 66.67% (10/15) | 86.67% (13/15) | 60.00% (3/5) | **100.0%** (5/5) | 90.00% (18/20) | **1.60 / 2.0** | **2.00 / 2.0** | **100%** (15/15) | $0.00135 |
| **Strategy D** (Mở rộng) | **Hybrid: Dense (C) + BM25 Okapi qua RRF** | **73.33%** (11/15) | **100.0%** (15/15) | **80.00%** (4/5) | **100.0%** (5/5) | **100.0%** (20/20) | **1.60 / 2.0** | **2.00 / 2.0** | **100%** (15/15) | $0.00135 |

- **Embedding Model:** OpenAI `text-embedding-3-small` (1536 chiều, cosine distance `<=>`).
- **Vector Store:** PostgreSQL 16.16 kết hợp extension `pgvector 0.8.7` chạy Docker local.
- **Tổng chi phí toàn bộ pipeline:** **$0.00404 USD** (~100 VNĐ cho toàn bộ 20 câu hỏi và 365 chunks).
- **Tính bất biến & Idempotency:** Hỗ trợ `ON CONFLICT (strategy, chunk_id) DO UPDATE`, nạp lại không bị duplicate bản ghi.

---

## 2. Hướng dẫn cài đặt & Chạy tái lập (Quickstart & Reproduction)

### Bước 1: Khởi động cơ sở dữ liệu PostgreSQL + pgvector
Đảm bảo đã cài đặt Docker Desktop và khởi động container:
```bash
cd starter
docker compose -p day6_student_starter up -d --wait
```
Kiểm tra kết nối và extension:
```bash
python scripts/check_setup.py
```
*(Kỳ vọng: Hiện thông tin database PostgreSQL 16, pgvector 0.8.7 và thông báo `Setup OK`).*

### Bước 2: Cài đặt thư viện Python
Sử dụng môi trường ảo Python 3.11+:
```bash
pip install -r starter/requirements.txt
```

### Bước 3: Cấu hình biến môi trường
Tạo file `starter/.env` (từ mẫu `starter/.env.example`) và điền API key:
```env
DATABASE_URL=postgresql://student:student_password_day6@127.0.0.1:55433/rag_ingestion
OPENAI_API_KEY=sk-...your-openai-api-key...
```

### Bước 4: Chạy toàn bộ pipeline & benchmark
Đứng từ thư mục gốc của repository, chạy lệnh để thực thi 3 chiến lược cốt lõi (A, B, C):
```bash
python -m starter.src.run
```
*(Tùy chọn) Chạy kiểm chứng mở rộng mô hình **Hybrid Retrieval** (Dense Vector + BM25 Okapi + RRF):*
```bash
python -m starter.src.run_hybrid
```
Lệnh trên sẽ tự động:
1. Đọc và chuẩn hóa 49 trang nội dung hợp lệ từ `data/TAKING STOCK March 2025_VNM_FINAL.pdf`.
2. Tạo chunks và vector embedding cho các chiến lược và lưu vào PostgreSQL.
3. Chạy đánh giá 20 câu benchmark (15 dev / 5 test).
4. Xuất toàn bộ thống kê, log và traces vào thư mục `results/`.

---

## 3. Cấu trúc thư mục nộp bài

```text
.
├── STUDENT.md                              # Thông tin học viên
├── README.md                               # Hướng dẫn chạy, kết quả và đề bài
├── data/
│   ├── TAKING STOCK March 2025_VNM_FINAL.pdf  # Tài liệu gốc World Bank (CC BY 3.0 IGO)
│   ├── manifest.json                       # Chi tiết nguồn, giấy phép, quy tắc lọc trang
│   └── format-example.json                 # Định dạng mẫu
├── benchmark/
│   └── questions.json                      # 20 câu hỏi (15 dev / 5 test) kèm gold evidence
├── artifacts/
│   └── experiment_log.csv                  # Nhật ký giả thuyết và chỉ số 3 chiến lược
├── results/                                # Toàn bộ artifacts và traces thực nghiệm
│   ├── metrics_summary.json                # Bảng số liệu Hit@1, Hit@3 chi tiết
│   ├── strategy_stats.json                 # Thống kê chunk count, ký tự, thời gian, chi phí
│   ├── ingestion_log.json                  # Log lọc 49 trang hợp lệ, 15 trang loại trừ
│   ├── manual_coherence_audit.json         # Đánh giá tính mạch lạc thủ công 10 vị trí
│   ├── manual_metadata_audit.json          # Đánh giá metadata & kiểm tra filter tuân thủ
│   ├── manual_grounding_audit.json         # Đánh giá chất lượng grounding 5 câu test
│   └── all_traces.json                     # Traces truy xuất top-3 cho toàn bộ câu hỏi
├── report/individual/
<<<<<<< HEAD
│   ├── 2A202603531_DuongThanhBinh.md       # Báo cáo phân tích chuyên sâu của học viên
│   └── MSSV_HoTen.md                       # File mẫu trắng ban đầu của ban tổ chức
=======
│   └── 2A202603531_HoTen.md                # Báo cáo phân tích chuyên sâu của học viên
>>>>>>> cbcb085c7f4f58eeefcca6bb8efbb9a916855478
└── starter/                                # Khung mã nguồn pipeline
    ├── docker-compose.yml                  # Cấu hình container pgvector local
    ├── requirements.txt                    # Danh sách thư viện phụ thuộc
    ├── scripts/check_setup.py              # Script kiểm tra môi trường
    └── src/                                # Mã nguồn pipeline hoàn thiện
        ├── ingestion.py                    # Đọc PDF, chuẩn hóa văn bản, lọc trang
        ├── chunking.py                     # Thuật toán chunking Fixed-size & Paragraph
        ├── embeddings.py                   # Module vector hóa với OpenAI API
        ├── store.py                        # Kho lưu trữ PostgreSQL + pgvector
        ├── evaluation.py                   # Đánh giá Hit@1, Hit@3, lọc metadata
        └── run.py                          # Pipeline điều phối thực nghiệm tự động
```

---

# [Tham chiếu] Đề bài gốc — Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.
