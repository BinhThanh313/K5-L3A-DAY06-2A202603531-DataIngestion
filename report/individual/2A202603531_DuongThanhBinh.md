# Báo cáo cá nhân — Day 6: Data Ingestion Engineering

## 1. Dữ liệu và pipeline

### Chủ đề, nguồn/quyền, số trang và quy tắc tính trang
- **Chủ đề:** Báo cáo cập nhật tình hình kinh tế vĩ mô Việt Nam năm 2024-2025 và chuyên đề phát triển hệ sinh thái giao thông điện, hạ tầng sạc, tác động lưới điện và khí thải nhà kính đến năm 2050.
- **Nguồn tài liệu:** [World Bank Open Knowledge Repository (content)](https://openknowledge.worldbank.org/server/api/core/bitstreams/a7930507-99b0-42bd-9c6e-7519320ca09b/content) (`data/TAKING STOCK March 2025_VNM_FINAL.pdf`).
- **Giấy phép:** **CC BY 3.0 IGO** (Creative Commons Attribution 3.0 IGO), xuất bản bởi Ngân hàng Tái thiết và Phát triển Quốc tế / Ngân hàng Thế giới (World Bank). Hoàn toàn được phép sử dụng công khai cho mục đích nghiên cứu và kỹ thuật dữ liệu.
- **Quy tắc tính trang:**
  - Tổng số trang vật lý của file PDF: **64 trang**.
  - **15 trang loại trừ hợp lệ theo GUIDE.md:**
    - Trang trống 0 ký tự: trang 2, 62, 63 (3 trang).
    - Trang bìa và tiêu đề: trang 1, 3 (2 trang).
    - Bản quyền & tuyên bố từ chối trách nhiệm: trang 4 (1 trang).
    - Mục lục và danh sách hình/bảng: trang 5, 6, 7 (3 trang).
    - Bảng từ viết tắt tiếng Anh & tiếng Việt: trang 8, 9 (2 trang).
    - Lời cảm ơn: trang 10 (1 trang).
    - Trang bìa phân cách chương: trang 19, 42 (2 trang).
    - Bìa sau thông tin liên hệ: trang 64 (1 trang).
  - **Số trang nội dung hợp lệ được đưa vào pipeline:** **49 trang** (vượt xa yêu cầu tối thiểu ≥ 30 trang), gồm:
    - *Tóm lược tổng quan:* Trang vật lý 11–18 (8 trang, số in La Mã `i–viii`, 21.905 ký tự).
    - *Chương 1 (Diễn biến và triển vọng kinh tế):* Trang vật lý 20–41 (22 trang, số in `10–31`, 51.986 ký tự).
    - *Chương 2 (Chuyển đổi sang xe điện):* Trang vật lý 43–61 (19 trang, số in `33–51`, 43.606 ký tự).
    - Tổng cộng: **117.497 ký tự đã chuẩn hóa**.

### Chuẩn hóa, lỗi và giới hạn
- **Chuẩn hóa văn bản (`normalize_text`):** Áp dụng chuẩn hóa Unicode NFC (`unicodedata.normalize('NFC')`), chuẩn hóa xuống dòng (`\r\n` $\rightarrow$ `\n`), nối lại các từ bị ngắt bởi dấu gạch ngang (`-`) ở cuối dòng do căn lề văn bản hai bên, gộp các dòng văn bản cùng đoạn văn nhưng giữ nguyên phân cách ranh giới tiêu đề mục (`1.1`, `2.1`), bảng (`Bảng`), hình ảnh (`Hình`), nguồn (`Nguồn:`), và dấu gạch đầu dòng (`•`).
- **Xử lý trang rỗng & trang lỗi:** Module `starter/src/ingestion.py` tự động phát hiện và ghi nhận vào `results/ingestion_log.json`. Cụ thể phát hiện 3 trang trống (2, 62, 63) với 0 ký tự và phân loại 12 trang phi nội dung theo đúng `data/manifest.json`.
- **Giới hạn:** Một số bảng số liệu phức tạp trong PDF (như Bảng 0.1, Bảng 1.1) khi trích xuất text thuần bằng `pypdf` sẽ thành chuỗi số liên tục mà mất đi quan hệ lưới hai chiều (grid layout). Do đó các câu hỏi benchmark tập trung vào các đoạn văn trích dẫn số liệu có ngữ cảnh tường minh trong bài viết.

### Embedding Model, Vector Store và Chi phí
- **Embedding Model:** `text-embedding-3-small` của OpenAI, số chiều `1536`, tính toán khoảng cách cosine trên PostgreSQL qua toán tử `<=>`.
- **Vector Store:** PostgreSQL 16.16 kết hợp extension `pgvector 0.8.7` chạy trong container Docker local (`ports: 127.0.0.1:55433`).
- **Lưu trữ bền vững & Chống nhân bản (Idempotency):**
  - Sử dụng bảng `public.document_chunks` với khóa chính `(strategy, chunk_id)`.
  - Thực hiện lệnh `INSERT ... ON CONFLICT (strategy, chunk_id) DO UPDATE`.
  - Khi re-ingest tài liệu cùng phiên bản, số lượng bản ghi kiểm tra trước và sau nạp lại đều duy trì chính xác **365 chunks** (thời gian upsert lại chỉ mất 0.67s, không bị duplicate bản ghi).
- **Chi phí thực tế:**
  - Embedding Strategy A: 65.143 tokens ($\approx$ $0.00130 USD)
  - Embedding Strategy B: 67.671 tokens ($\approx$ $0.00135 USD)
  - Embedding Strategy C: dùng lại chunks của B, tạo index riêng ($\approx$ $0.00135 USD)
  - Embedding 20 câu query: 1.655 tokens ($\approx$ $0.00003 USD)
  - **Tổng chi phí toàn bộ thí nghiệm:** **$0.00404 USD** (rất tiết kiệm, dưới 100 VNĐ).

### Metadata bắt buộc và trường lọc bổ sung
- **Metadata bắt buộc:** `document_id` (`wb-taking-stock-2025-03`), `source` (`data/TAKING STOCK March 2025_VNM_FINAL.pdf`), `page` (số trang vật lý 1–64).
- **Trường lọc bổ sung đã kiểm chứng:**
  - `section`: Gồm 3 giá trị phân biệt được trích từ Mục lục trang 5:
    1. `tom_luoc`: Phần Tóm lược tổng quan (trang vật lý 11–18).
    2. `kinh_te_vi_mo`: Chương 1 - Diễn biến và triển vọng kinh tế (trang vật lý 20–41).
    3. `chuyen_doi_xe_dien`: Chương 2 - Chuyển đổi sang xe điện (trang vật lý 43–61).
  - `printed_page`: Số trang in trên tài liệu gốc (dạng La Mã `i–viii` hoặc số thường `10–51`).
  - `year`: `2025` (năm xuất bản báo cáo).
  - `sub_section`: Tiêu đề mục con chi tiết (ví dụ: `1.1 Diễn biến kinh tế gần đây`, `2.7 Khuyến nghị chính sách`).

---

## 2. Cấu hình và benchmark

### Mô tả 3 Chiến lược (A / B / C)
- **Chiến lược A (Baseline):** Fixed-size chunking với kích thước cửa sổ cố định `size = 500` ký tự, độ gối `overlap = 50` ký tự. Gắn đầy đủ metadata; truy xuất Top-3 tương đồng cosine không áp dụng lọc metadata.
- **Chiến lược B (Structure-aware Chunking):** Chunking theo đoạn văn và tiêu đề (`by_paragraph`) với ngưỡng giới hạn `max_size = 500` ký tự. Nếu đoạn văn vượt quá 500 ký tự sẽ được ngắt thông minh theo ranh giới câu (`.` `!` `?` `:` `;`). Giữ nguyên embedding model và vector store, truy xuất Top-3 không lọc.
  *(So sánh A–B nhằm đánh giá riêng rẽ tác động của kỹ thuật chunking).*
- **Chiến lược C (Structure-aware Chunking + Metadata Filtering):** Sử dụng chính xác cấu trúc chunk của Chiến lược B, nhưng khi truy xuất câu hỏi có chỉ định phạm vi phân tích, hệ thống áp dụng câu điều kiện SQL `WHERE section = %s` trực tiếp trong truy vấn vector database.
  *(So sánh B–C giữ nguyên chunk và model để cô lập và đo lường chính xác tác động của Metadata Filtering).*

### Benchmark: Phân chia Dev / Test và Evidence Verification
- Bộ câu hỏi gồm **20 câu hỏi** được biên soạn và chốt trước khi chạy thực nghiệm tại `benchmark/questions.json`:
  - **Tập Dev (15 câu - `q01` đến `q15`):** Gồm 5 câu trực tiếp, 3 câu diễn đạt lại, 3 câu điều kiện/ngoại lệ, và 4 câu kiểm tra metadata filter.
  - **Tập Test (5 câu - `q16` đến `q20`):** Giữ nguyên làm tập holdout độc lập, gồm 1 câu trực tiếp, 1 câu diễn đạt lại, 1 câu điều kiện/ngoại lệ, và 2 câu kiểm tra metadata filter (`q16` lọc `kinh_te_vi_mo`, `q20` lọc `chuyen_doi_xe_dien`).
- Toàn bộ bằng chứng `evidence_quote` đã được xác minh 100% tồn tại nguyên văn trong văn bản đã chuẩn hóa của trang nguồn `gold_page`. Với các câu hỏi tổng quát có số liệu xuất hiện ở nhiều phần, benchmark ghi nhận rõ ràng các nguồn thay thế hợp lệ trong `alternative_gold` (ví dụ `q01`, `q06`, `q15`, `q16`).
- **Tiêu chí lựa chọn chiến lược (đã chốt trước khi xem kết quả test):** Ưu tiên cấu hình có `Evidence Hit@3` cao nhất trên tập Dev; nếu bằng nhau, xét tiếp `Hit@1` trên Dev, điểm trung bình `Chunk Coherence` (/2), tỷ lệ tuân thủ bộ lọc metadata và chi phí/thời gian tài nguyên.

---

## 3. Kết quả thực nghiệm

### Bảng 1: Hiệu năng Truy xuất và Tài nguyên Pipeline

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian Ingest / Truy xuất | Chi phí Embedding |
|---|---|---|---|---|---|---|
| **A (Fixed 500/50)** | Dev | 8/15 (53.33%) | 11/15 (73.33%) | 279 chunks | Ingest: 6.11s | 65.143 tokens |
| | Test | 3/5 (60.00%) | 3/5 (60.00%) | (Mean: 462 chars) | Top-3 query: 0.44s | ~$0.00130 USD |
| | **All** | **11/20 (55.00%)** | **14/20 (70.00%)** | | | |
| **B (Paragraph 500)** | Dev | 10/15 (66.67%) | 12/15 (80.00%) | 365 chunks | Ingest: 2.80s | 67.671 tokens |
| | Test | 3/5 (60.00%) | 5/5 (100.00%) | (Mean: 368 chars) | Top-3 query: 0.46s | ~$0.00135 USD |
| | **All** | **13/20 (65.00%)** | **17/20 (85.00%)** | | | |
| **C (Para + Filter)** | Dev | 10/15 (66.67%) | 13/15 (86.67%) | 365 chunks | Ingest: 2.66s | 67.671 tokens |
| | Test | 3/5 (60.00%) | 5/5 (100.00%) | (Mean: 368 chars) | Top-3 query: 0.39s | ~$0.00135 USD |
| | **All** | **13/20 (65.00%)** | **18/20 (90.00%)** | | | |

---

### Bảng 2: Đánh giá Thủ công 5 Khía cạnh Chất lượng

| Strategy | Coherence trung bình /2 (n=10) | Metadata đủ và đúng (n=15 bản ghi test) | Filter tuân thủ (câu có filter) | Grounding 0/1/2 (số câu test) và TB /2 |
|---|---|---|---|---|
| **A (Fixed)** | 0.90 / 2 (0 mức 2, 9 mức 1, 1 mức 0) | 15/15 (100%) | Dev: 1/4 (25%) \| Test: 2/2 (100%) | 0 câu 0đ, 2 câu 1đ, 3 câu 2đ $\rightarrow$ **1.60 / 2** |
| **B (Paragraph)** | 1.70 / 2 (7 mức 2, 3 mức 1, 0 mức 0) | 15/15 (100%) | Dev: 1/4 (25%) \| Test: 2/2 (100%) | 0 câu 0đ, 0 câu 1đ, 5 câu 2đ $\rightarrow$ **2.00 / 2** |
| **C (Para + Filter)** | 1.70 / 2 (7 mức 2, 3 mức 1, 0 mức 0) | 15/15 (100%) | Dev: 4/4 (100%) \| Test: 2/2 (100%) | 0 câu 0đ, 0 câu 1đ, 5 câu 2đ $\rightarrow$ **2.00 / 2** |

- **Đường dẫn artifacts và audit đầy đủ:**
  - Full trace truy xuất Top-3: [results/all_traces.json](file:///d:/VinAI20K/K5-L3A-DAY06-2A202603531-DataIngestion/results/all_traces.json) (và tách riêng `traces_A.json`, `traces_B.json`, `traces_C.json`).
  - Mẫu chấm Coherence: [results/manual_coherence_audit.json](file:///d:/VinAI20K/K5-L3A-DAY06-2A202603531-DataIngestion/results/manual_coherence_audit.json).
  - Kiểm tra Metadata: [results/manual_metadata_audit.json](file:///d:/VinAI20K/K5-L3A-DAY06-2A202603531-DataIngestion/results/manual_metadata_audit.json).
  - Chấm Grounding Quality: [results/manual_grounding_audit.json](file:///d:/VinAI20K/K5-L3A-DAY06-2A202603531-DataIngestion/results/manual_grounding_audit.json).
  - Thống kê phân bố chunk & thời gian: [results/strategy_stats.json](file:///d:/VinAI20K/K5-L3A-DAY06-2A202603531-DataIngestion/results/strategy_stats.json).
  - Nhật ký tổng hợp: [artifacts/experiment_log.csv](file:///d:/VinAI20K/K5-L3A-DAY06-2A202603531-DataIngestion/artifacts/experiment_log.csv).

---

## 4. Phân tích và kết luận

### So sánh A–B (Tác động của Chunking)
- **Hiệu năng truy xuất:** Chiến lược B vượt trội Chiến lược A trên cả Dev Hit@3 (80.0% so với 73.33%) và Test Hit@3 (100.0% so với 60.00%). Tổng thể cả bộ câu hỏi, Hit@3 tăng từ **70.0% lên 85.0%**.
- **Tính mạch lạc (Chunk Coherence):** Điểm trung bình Coherence của B đạt **1.70/2**, cao hơn đáng kể so với **0.90/2** của A. Lý do là Strategy A cắt cố định theo 500 ký tự nên có đến 9/10 mẫu kiểm tra bị cắt cụt từ ngữ ở cuối dòng (ví dụ `pos_01`: *"trong năm 2024. Mặ"*, `pos_09`: *"suy giảm và tă"*), làm mất liên kết cú pháp và mất chủ ngữ mệnh đề tiếp theo. Ngược lại, Strategy B ngắt theo ranh giới câu và đoạn văn nên 7/10 chunk giữ trọn vẹn ngữ cảnh độc lập.
- **Tác động đến Grounding:** Trên 5 câu test, Strategy A có 2 câu chỉ đạt 1/2 điểm grounding (`q17`, `q18`) do bằng chứng bị xé lẻ giữa hai chunk liền kề. Strategy B đạt trọn vẹn **2.0/2** điểm grounding trên toàn bộ 5 câu test.

### So sánh B–C (Tác động của Metadata Filtering)
- **Hiệu năng truy xuất:** B và C dùng chung cấu trúc chunk, nhưng khi bật bộ lọc metadata SQL `section` ở Chiến lược C, Dev Hit@3 tăng từ **80.00% lên 86.67%** (giúp sửa lỗi thành công ở câu `q05`), đưa tỷ lệ Hit@3 tổng thể đạt **90.00% (18/20 câu)**.
- **Khả năng định hướng phạm vi (Compliance):** Trên các câu hỏi có yêu cầu điều kiện filter (như `q05`, `q08`, `q11`, `q15`, `q16`, `q20`), Chiến lược C đạt **100% tuân thủ** (6/6 câu), loại bỏ hoàn toàn việc vector search bị nhầm lẫn giữa đoạn tóm tắt ở phần đầu báo cáo và đoạn phân tích chi tiết ở các chương chuyên đề.
- **Tốc độ truy xuất:** Nhờ mệnh đề `WHERE section = %s` thu hẹp không gian tìm kiếm, thời gian truy vấn của C giảm xuống **0.385s** (nhanh hơn mức 0.460s của B).

### Phân tích 2 ví dụ lỗi điển hình (Failure Analysis)

1. **Ví dụ lỗi 1: Chia cửa sổ cố định làm đứt gãy bằng chứng ở Chiến lược A (Câu `q06`, Tập Dev)**
   - *Câu hỏi:* Điều kiện để NHNN cho phép các ngân hàng mở rộng cho vay ngoài trần tín dụng năm 2024?
   - *Nguồn & Gold Page:* Báo cáo trang 32 (`kinh_te_vi_mo`).
   - *Bằng chứng chuẩn:* `"cho phép một số ngân hàng có nền vốn vững được mở rộng phạm vi cho vay ngoài chỉ tiêu sau khi đã đạt 80% hạn mức vào tháng 8, mà không phải xin phê duyệt để nâng trần tín dụng."`
   - *Hiện tượng ở A:* Chunk `wb-taking-stock-2025-03_p32_fix_01` bị cắt cụt ở giữa câu tại chữ *"sau khi đã đạt 80% hạn mức vào tháng 8"*, còn vế đuôi *"mà không phải xin phê duyệt..."* lại rơi sang chunk `_fix_02`. Dù trang 32 nằm ở Rank 1, nhưng không một chunk nào chứa đủ bằng chứng trọn vẹn $\rightarrow$ **Evidence Hit@3 = False (Fail)**.
   - *Khắc phục ở B và C:* Nhờ chunk theo đoạn văn, câu quy định này nằm trọn vẹn trong chunk `wb-taking-stock-2025-03_p32_para_02` $\rightarrow$ **Đạt Hit@1 ngay lập tức với score 0.760**.

2. **Ví dụ lỗi 2: Tranh chấp ngữ nghĩa giữa Tóm tắt và Chương chi tiết được giải quyết nhờ Filter (Câu `q05`, Tập Dev)**
   - *Câu hỏi:* Theo phân tích chi tiết tại Chương 1, NHNN đã bán bao nhiêu tỷ USD dự trữ ngoại hối và thu hút thanh khoản qua OMO, tín phiếu kho bạc?
   - *Nguồn & Gold Page:* Báo cáo trang 30 (`kinh_te_vi_mo`).
   - *Hiện tượng ở B:* Do phần Tóm lược tổng quan ở trang 13 (`tom_luoc`) cũng có đoạn văn tóm tắt tương tự về bán 9,4 tỷ USD ngoại hối, mô hình embedding ngữ nghĩa thuần túy ở Strategy B đã ưu tiên xếp chunk trang 13 vào Rank 1 (score 0.635) và Rank 3 (score 0.607). Chunk trang 30 bị đẩy văng khỏi Top-3 $\rightarrow$ **Evidence Hit@3 = False (Fail)**.
   - *Khắc phục ở C:* Khi áp dụng filter `section = 'kinh_te_vi_mo'`, hệ thống loại trừ triệt để các chunk thuộc `tom_luoc`, đưa chunk trang 30 lên Rank 2 $\rightarrow$ **Evidence Hit@3 = True (Success)**.

### Chiến lược được lựa chọn, Đánh đổi và Điều kiện áp dụng
- **Chiến lược được chọn: CHIẾN LƯỢC C (Paragraph-aware Chunking + Metadata Filtering).**
  - Đạt chỉ số cao nhất: Dev Hit@3 = 86.67%, Test Hit@3 = 100.0%, Overall Hit@3 = 90.00%.
  - Giữ được trọn vẹn tính mạch lạc của câu văn (Coherence 1.70/2) và chất lượng bằng chứng (Grounding 2.0/2).
  - Tốc độ truy vấn nhanh nhất (0.385s cho 20 câu hỏi).
- **Đánh đổi (Trade-off):**
  - Chia theo đoạn văn làm tăng số lượng chunk (365 chunks so với 279 chunks của A, tăng ~30% số lượng chunk và vector lưu trữ).
  - Phụ thuộc vào chất lượng của metadata đầu vào: nếu người dùng cung cấp filter sai hoặc câu hỏi không rõ ngữ cảnh, việc hard filter có thể dẫn đến hiện tượng loại trừ nhầm các thông tin giá trị ở các section khác.
- **Điều kiện áp dụng:** Rất phù hợp cho các kho tri thức tài liệu có cấu trúc phân cấp rõ ràng (văn bản quy phạm pháp luật, sổ tay học vụ, tài liệu kỹ thuật nhiều chương, báo cáo thường niên chia phần).

### Hạn chế và Hướng cải tiến
- **Hạn chế:**
  - Tập test nhỏ (5 câu) mang tính chất kiểm tra chất lượng định tính, chưa đủ lớn để ngoại suy thống kê cho mọi trường hợp.
  - Bộ bóc tách PDF chưa tái tạo lại được cấu trúc bảng biểu 2 chiều (matrix tables).
  - Đánh giá thủ công Coherence và Grounding do tác giả thực hiện nên có nguy cơ thiên lệch chủ quan.
- **Hướng cải tiến nếu mở rộng:**
  - Bổ sung Hybrid Retrieval (kết hợp Dense Cosine Similarity + BM25 keyword search) để xử lý các câu hỏi chứa mã văn bản đặc thù hoặc tên riêng viết tắt (như Quyết định 876/QĐ-TTg, VNEEP3, DPPA).
  - Thêm cơ chế Parser bảng biểu chuyên dụng (như `pdfplumber` hoặc mô hình OCR layout) để chuyển các bảng thống kê kinh tế thành định dạng Markdown Table có cấu trúc trước khi chunking.
