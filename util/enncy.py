# 言溪题库
import requests
from config import enncy_key
from util.ocr import ocr_form_url_image


def search(question: str):
    """搜索题库获取答案"""
    if not enncy_key:
        print("题库密钥未配置")
        return None
        
    query_params = {
        "token": enncy_key,
        "q": question,
    }
    
    try:
        response = requests.get(url="https://tk.enncy.cn/query", params=query_params)
        if response.status_code == 200:
            return response.text
        else:
            print(f"题库搜索失败，状态码: {response.status_code}")
            return None
    except Exception as e:
        print(f"题库搜索异常: {e}")
        return None


def ocr_with_search(image_url: str):
    """先OCR识别图片，再用识别结果搜索题库"""
    ocr_result = ocr_form_url_image(image_url)
    if ocr_result:
        return search(" ".join(ocr_result))
    return None