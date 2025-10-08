import streamlit as st
import pandas as pd
import numpy as np
import numpy_financial as npf # Cần đảm bảo thư viện này được cài đặt trong môi trường
from google import genai
from google.genai.errors import APIError
from docx import Document
import io
import json
import time

# --- Định nghĩa Cấu trúc Dữ liệu mong muốn từ AI ---
# Đây là schema JSON mà Gemini sẽ cần tuân theo để trích xuất dữ liệu
EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "initial_investment": {"type": "INTEGER", "description": "Tổng vốn đầu tư ban đầu của dự án (ví dụ: 1000000000)."},
        "project_life_years": {"type": "INTEGER", "description": "Dòng đời dự án bằng số năm."},
        "annual_revenue": {"type": "INTEGER", "description": "Doanh thu trung bình hàng năm dự kiến."},
        "annual_cost": {"type": "INTEGER", "description": "Tổng chi phí hoạt động hàng năm dự kiến (chưa bao gồm khấu hao và thuế)."},
        "wacc_percent": {"type": "NUMBER", "description": "Tỷ lệ Chi phí vốn bình quân (WACC) dưới dạng phần trăm (ví dụ: 10.5)."},
        "tax_rate_percent": {"type": "NUMBER", "description": "Tỷ lệ Thuế thu nhập doanh nghiệp dưới dạng phần trăm (ví dụ: 20)."}
    },
    "required": ["initial_investment", "project_life_years", "annual_revenue", "annual_cost", "wacc_percent", "tax_rate_percent"]
}

# --- Cấu hình Trang Streamlit ---
st.set_page_config(
    page_title="App Đánh Giá Phương Án Kinh Doanh",
    layout="wide"
)

st.title("Ứng dụng Đánh giá Phương án Kinh doanh 📈")
st.markdown("Sử dụng AI để trích xuất dữ liệu tài chính từ file Word và tính toán hiệu quả dự án.")

# Khởi tạo state để lưu dữ liệu đã trích xuất
if 'extracted_data' not in st.session_state:
    st.session_state['extracted_data'] = None
if 'cash_flow_df' not in st.session_state:
    st.session_state['cash_flow_df'] = None
if 'metrics' not in st.session_state:
    st.session_state['metrics'] = None

# --- Helper functions ---

@st.cache_data
def read_docx_content(uploaded_file):
    """Đọc nội dung văn bản từ file Word (.docx)."""
    try:
        # Sử dụng io.BytesIO để đọc file Word từ Streamlit UploadedFile
        doc = Document(io.BytesIO(uploaded_file.getvalue()))
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return "\n".join(full_text)
    except Exception as e:
        st.error(f"Lỗi khi đọc file Word: {e}")
        return None

def extract_data_with_ai(docx_content, api_key):
    """
    1. Yêu cầu AI (Gemini) trích xuất dữ liệu tài chính có cấu trúc.
    """
    try:
        client = genai.Client(api_key=api_key)
        model_name = 'gemini-2.5-flash-preview-05-20'

        system_prompt = "Bạn là một chuyên gia phân tích tài chính. Nhiệm vụ của bạn là đọc nội dung tệp Word của phương án kinh doanh và trích xuất sáu chỉ số tài chính sau thành đối tượng JSON có cấu trúc chính xác như schema đã cung cấp. KHÔNG được thêm bất kỳ văn bản giải thích nào khác ngoài đối tượng JSON. Nếu không tìm thấy thông tin cụ thể, hãy cố gắng ước tính hợp lý hoặc để giá trị 0/None nếu không thể ước tính."

        user_query = f"""
        Trích xuất các thông tin sau từ nội dung Phương án Kinh doanh dưới đây:
        1. Vốn đầu tư (initial_investment)
        2. Dòng đời dự án (project_life_years)
        3. Doanh thu hàng năm (annual_revenue)
        4. Chi phí hàng năm (annual_cost)
        5. WACC (%) (wacc_percent)
        6. Thuế suất (%) (tax_rate_percent)

        Nội dung Phương án Kinh doanh:
        ---
        {docx_content}
        ---
        """
        
        # Cấu hình để nhận phản hồi JSON
        config = {
            "responseMimeType": "application/json",
            "responseSchema": EXTRACTION_SCHEMA
        }

        response = client.models.generate_content(
            model=model_name,
            contents=[{"role": "user", "parts": [{"text": user_query}]}],
            system_instruction={"parts": [{"text": system_prompt}]},
            config=config
        )

        # Kết quả sẽ là một chuỗi JSON
        json_string = response.candidates[0].content.parts[0].text
        return json.loads(json_string)

    except APIError as e:
        st.error(f"Lỗi gọi Gemini API: Vui lòng kiểm tra Khóa API hoặc giới hạn sử dụng. Chi tiết lỗi: {e}")
        return None
    except KeyError:
        st.error("Lỗi: Không tìm thấy Khóa API 'GEMINI_API_KEY'. Vui lòng kiểm tra cấu hình Secrets.")
        return None
    except json.JSONDecodeError:
        st.error("Lỗi giải mã JSON từ AI. Nội dung trích xuất không đúng định dạng. Vui lòng thử lại với tài liệu rõ ràng hơn.")
        return None
    except Exception as e:
        st.error(f"Đã xảy ra lỗi không xác định: {e}")
        return None

def calculate_project_metrics(data):
    """
    2. Xây dựng bảng dòng tiền và 3. Tính toán các chỉ số đánh giá.
    """
    try:
        V = data['initial_investment']
        T = data['project_life_years']
        R = data['annual_revenue']
        C = data['annual_cost']
        WACC = data['wacc_percent'] / 100
        TaxRate = data['tax_rate_percent'] / 100

        # Tính toán Dòng tiền Thuần Hàng năm (Annual Net Cash Flow - NCF)
        # Giả định NCF = (Doanh thu - Chi phí) * (1 - Thuế suất), bỏ qua Khấu hao & Vốn lưu động
        NCF_annual = (R - C) * (1 - TaxRate)

        # --- Xây dựng Bảng Dòng tiền (Yêu cầu 2) ---
        years = [i for i in range(T + 1)]
        
        # Dòng tiền ban đầu (Năm 0)
        cash_flows = [-V]
        cash_flows.extend([NCF_annual] * T)
        
        cf_df = pd.DataFrame({
            'Năm': years,
            'Dòng tiền thuần (NCF)': cash_flows,
            'Hệ số Chiết khấu': [1 / ((1 + WACC)**y) for y in years],
            'Dòng tiền Chiết khấu (DCF)': [cf * (1 / ((1 + WACC)**y)) for y, cf in zip(years, cash_flows)]
        })
        cf_df['Dòng tiền Chiết khấu Tích lũy'] = cf_df['Dòng tiền Chiết khấu (DCF)'].cumsum()

        # --- Tính các chỉ số (Yêu cầu 3) ---
        
        # 1. NPV (Net Present Value)
        npv_value = npf.npv(WACC, cash_flows)

        # 2. IRR (Internal Rate of Return)
        try:
            irr_value = npf.irr(cash_flows)
        except:
            irr_value = np.nan # NaN nếu không tìm được nghiệm
        
        # 3. PP (Payback Period) - Thời gian hoàn vốn
        cumulative_cf = np.cumsum(cash_flows)
        # Tìm năm đầu tiên tích lũy > 0
        payback_index = np.where(cumulative_cf >= 0)[0]
        if len(payback_index) > 0:
            year_pp = payback_index[0]
            if year_pp > 0:
                # Tính toán nội suy: Năm trước khi hòa vốn + (Vốn còn thiếu / Dòng tiền năm hòa vốn)
                cumulative_cf_prev = cumulative_cf[year_pp - 1]
                cf_current = cash_flows[year_pp]
                # Vốn còn thiếu
                remaining_capital = -cumulative_cf_prev
                pp_value = (year_pp - 1) + (remaining_capital / cf_current)
            else:
                pp_value = 0 # Hoàn vốn ngay trong năm đầu
        else:
            pp_value = "Không hoàn vốn"

        # 4. DPP (Discounted Payback Period) - Thời gian hoàn vốn có chiết khấu
        cumulative_dcf = cf_df['Dòng tiền Chiết khấu (DCF)'].cumsum().values
        # Tìm năm đầu tiên tích lũy chiết khấu > 0
        discounted_payback_index = np.where(cumulative_dcf >= 0)[0]
        if len(discounted_payback_index) > 0:
            year_dpp = discounted_payback_index[0]
            if year_dpp > 0:
                # Tính toán nội suy
                cumulative_dcf_prev = cumulative_dcf[year_dpp - 1]
                dcf_current = cf_df['Dòng tiền Chiết khấu (DCF)'].iloc[year_dpp]
                remaining_capital_d = -cumulative_dcf_prev
                dpp_value = (year_dpp - 1) + (remaining_capital_d / dcf_current)
            else:
                dpp_value = 0
        else:
            dpp_value = "Không hoàn vốn"

        metrics = {
            "NPV": npv_value,
            "IRR": irr_value,
            "PP": pp_value,
            "DPP": dpp_value,
            "WACC": WACC
        }

        return cf_df, metrics
    
    except Exception as e:
        st.error(f"Lỗi khi tính toán chỉ số tài chính: {e}. Vui lòng kiểm tra dữ liệu đã trích xuất.")
        return None, None

def get_ai_analysis_report(metrics_data, api_key):
    """
    4. Chức năng yêu cầu AI phân tích các chỉ số hiệu quả dự án.
    """
    try:
        client = genai.Client(api_key=api_key)
        model_name = 'gemini-2.5-flash'

        # Chuyển đổi dữ liệu metrics sang định dạng dễ đọc cho AI
        metrics_display = f"""
        - NPV (Giá trị hiện tại ròng): {metrics_data['NPV']:,.0f} VND
        - IRR (Tỷ suất sinh lời nội bộ): {metrics_data['IRR'] * 100:.2f}%
        - PP (Thời gian hoàn vốn): {metrics_data['PP']:.2f} năm
        - DPP (Thời gian hoàn vốn có chiết khấu): {metrics_data['DPP']:.2f} năm
        - WACC (Tỷ lệ chiết khấu): {metrics_data['WACC'] * 100:.2f}%
        """
        
        wacc = metrics_data['WACC']
        irr = metrics_data['IRR']
        
        # Đánh giá sơ bộ để định hướng phân tích của AI
        if metrics_data['NPV'] > 0 and irr > wacc:
            pre_analysis = "Dự án có khả năng sinh lời và được chấp nhận."
        elif metrics_data['NPV'] < 0 and irr < wacc:
            pre_analysis = "Dự án không có khả năng sinh lời và nên bị từ chối."
        else:
            pre_analysis = "Cần phân tích kỹ lưỡng hơn về rủi ro."

        prompt = f"""
        Bạn là một chuyên gia thẩm định và phân tích dự án đầu tư. 
        Hãy phân tích các chỉ số hiệu quả dự án dưới đây và đưa ra nhận định chuyên nghiệp, khách quan, bao gồm:
        1. Đánh giá tính khả thi (chấp nhận hay từ chối) dựa trên NPV và so sánh IRR với WACC.
        2. Nhận xét về tốc độ hoàn vốn dựa trên PP và DPP.
        3. Kết luận tổng thể và đề xuất (ví dụ: cần xem xét thêm rủi ro, dự án rất tốt, v.v.).

        Các chỉ số phân tích:
        {metrics_display}
        """

        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        return response.text

    except APIError as e:
        return f"Lỗi gọi Gemini API: Vui lòng kiểm tra Khóa API. Chi tiết lỗi: {e}"
    except Exception as e:
        # Xử lý trường hợp IRR, PP, DPP là chuỗi "Không hoàn vốn"
        return f"Đã xảy ra lỗi không xác định khi gọi AI phân tích: {e}. Có thể do dữ liệu không hợp lệ (ví dụ: dự án không hoàn vốn)."


# --- Giao diện Streamlit ---

api_key = st.secrets.get("GEMINI_API_KEY")

# --- Phần 1: Tải File và Trích xuất Dữ liệu ---
st.subheader("1. Trích xuất Dữ liệu Tài chính từ Phương án Kinh doanh (.docx)")
uploaded_file = st.file_uploader(
    "Tải file Word (.docx) chứa thông tin phương án kinh doanh:",
    type=['docx']
)

if uploaded_file:
    # Lấy nội dung file
    docx_content = read_docx_content(uploaded_file)
    st.info("Đã đọc nội dung file Word thành công. Sẵn sàng trích xuất dữ liệu.")

    if st.button("Tạo tác Lọc Dữ liệu (AI Extraction)", key="extract_btn"):
        if not api_key:
            st.error("Lỗi: Không tìm thấy Khóa API 'GEMINI_API_KEY'. Vui lòng cấu hình Streamlit Secrets.")
        elif docx_content:
            with st.spinner('Đang gửi nội dung file Word và chờ Gemini trích xuất dữ liệu có cấu trúc...'):
                extracted_data = extract_data_with_ai(docx_content, api_key)
                if extracted_data:
                    st.session_state['extracted_data'] = extracted_data
                    # Xóa các kết quả tính toán cũ
                    st.session_state['cash_flow_df'] = None
                    st.session_state['metrics'] = None
                    st.success("Trích xuất dữ liệu thành công!")

# --- Hiển thị Dữ liệu đã Lọc ---
if st.session_state['extracted_data']:
    data = st.session_state['extracted_data']
    st.subheader("Dữ liệu Dự án Đã Trích xuất (Kiểm tra và Chỉnh sửa nếu cần)")
    
    # Tạo Form để hiển thị và cho phép chỉnh sửa dữ liệu đã trích xuất
    with st.form("extracted_data_form"):
        col_inv, col_life, col_wacc, col_tax = st.columns(4)
        
        with col_inv:
            data['initial_investment'] = st.number_input(
                "Vốn đầu tư ban đầu (VND)",
                value=int(data.get('initial_investment', 0)),
                min_value=0, step=10000000
            )
        with col_life:
            data['project_life_years'] = st.number_input(
                "Dòng đời Dự án (Năm)",
                value=int(data.get('project_life_years', 1)),
                min_value=1, max_value=50, step=1
            )
        with col_wacc:
            data['wacc_percent'] = st.number_input(
                "WACC (%)",
                value=float(data.get('wacc_percent', 10.0)),
                min_value=0.1, max_value=100.0, step=0.1, format="%.2f"
            )
        with col_tax:
            data['tax_rate_percent'] = st.number_input(
                "Thuế suất TNDN (%)",
                value=float(data.get('tax_rate_percent', 20.0)),
                min_value=0.0, max_value=100.0, step=0.1, format="%.2f"
            )
            
        col_rev, col_cost, col_empty = st.columns(3)
        with col_rev:
            data['annual_revenue'] = st.number_input(
                "Doanh thu Hàng năm (VND)",
                value=int(data.get('annual_revenue', 0)),
                min_value=0, step=10000000
            )
        with col_cost:
            data['annual_cost'] = st.number_input(
                "Chi phí Hàng năm (VND)",
                value=int(data.get('annual_cost', 0)),
                min_value=0, step=10000000
            )
        
        submitted = st.form_submit_button("Cập nhật và Chuyển sang Tính toán")
        
        if submitted:
            st.session_state['extracted_data'] = data
            # Tính toán lại sau khi cập nhật
            cash_flow_df, metrics = calculate_project_metrics(data)
            st.session_state['cash_flow_df'] = cash_flow_df
            st.session_state['metrics'] = metrics
            st.success("Dữ liệu đã được cập nhật và sẵn sàng cho phân tích.")
        
    # Tự động tính toán lần đầu nếu chưa có kết quả
    if st.session_state['cash_flow_df'] is None:
        cash_flow_df, metrics = calculate_project_metrics(data)
        st.session_state['cash_flow_df'] = cash_flow_df
        st.session_state['metrics'] = metrics

# --- Phần 2 & 3: Bảng Dòng tiền và Chỉ số Đánh giá ---
if st.session_state['cash_flow_df'] is not None and st.session_state['metrics'] is not None:
    
    st.markdown("---")
    st.subheader("2. Bảng Dòng tiền Chiết khấu của Dự án")
    st.dataframe(st.session_state['cash_flow_df'].style.format({
        'Dòng tiền thuần (NCF)': '{:,.0f}',
        'Hệ số Chiết khấu': '{:.4f}',
        'Dòng tiền Chiết khấu (DCF)': '{:,.0f}',
        'Dòng tiền Chiết khấu Tích lũy': '{:,.0f}'
    }), use_container_width=True)

    st.markdown("---")
    st.subheader("3. Các Chỉ số Đánh giá Hiệu quả Dự án")
    
    metrics = st.session_state['metrics']
    
    col1, col2, col3, col4 = st.columns(4)
    
    col1.metric("WACC (Chi phí vốn)", f"{metrics['WACC'] * 100:.2f}%")
    
    # Hiển thị kết quả NPV và IRR
    npv_delta = "Dự án khả thi" if metrics['NPV'] > 0 else "Dự án không khả thi"
    irr_delta = "Chấp nhận dự án" if metrics['IRR'] >= metrics['WACC'] else "Từ chối dự án"
    
    col2.metric("NPV (Giá trị hiện tại ròng)", f"{metrics['NPV']:,.0f} VND", delta=npv_delta)
    col3.metric("IRR (Tỷ suất sinh lời nội bộ)", f"{metrics['IRR'] * 100:.2f}%", delta=irr_delta)

    # Hiển thị thời gian hoàn vốn
    pp_value = f"{metrics['PP']:.2f} năm" if isinstance(metrics['PP'], (int, float)) else metrics['PP']
    dpp_value = f"{metrics['DPP']:.2f} năm" if isinstance(metrics['DPP'], (int, float)) else metrics['DPP']

    col4.metric("PP (Thời gian hoàn vốn)", pp_value)
    col4.metric("DPP (Thời gian hoàn vốn có chiết khấu)", dpp_value)

    # --- Phần 4: Phân tích AI ---
    st.markdown("---")
    st.subheader("4. Phân tích Chỉ số Hiệu quả Dự án (AI)")
    
    if st.button("Yêu cầu AI Phân tích Chỉ số", key="analyze_btn"):
        if not api_key:
            st.error("Lỗi: Không tìm thấy Khóa API 'GEMINI_API_KEY'. Vui lòng cấu hình Streamlit Secrets.")
        else:
            # Xử lý trường hợp IRR, PP, DPP có thể là chuỗi 'Không hoàn vốn'
            # Đảm bảo các giá trị là số trước khi gửi cho AI để tránh lỗi
            metrics_for_ai = st.session_state['metrics'].copy()
            for key in ['PP', 'DPP']:
                if isinstance(metrics_for_ai[key], str):
                    metrics_for_ai[key] = 999.0 # Gán giá trị lớn để AI hiểu là 'Không hoàn vốn'
            
            with st.spinner('Đang gửi các chỉ số và chờ Gemini phân tích...'):
                ai_result = get_ai_analysis_report(metrics_for_ai, api_key)
                st.markdown("**Kết quả Phân tích từ Gemini AI:**")
                st.info(ai_result)

else:
    st.info("Vui lòng tải file Word lên và nhấn 'Tạo tác Lọc Dữ liệu' để bắt đầu quy trình.")
