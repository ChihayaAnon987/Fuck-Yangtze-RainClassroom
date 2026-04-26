from paddleocr import PaddleOCR
import cv2
import numpy as np
import requests

# 全局只初始化一次，避免重复加载
ocr = PaddleOCR(use_angle_cls=False, lang="ch")


def _download_image(url: str) -> np.ndarray:
    """下载网络图片并转换为OpenCV格式"""
    response = requests.get(url)
    response.raise_for_status()
    image_array = np.asarray(bytearray(response.content), dtype=np.uint8)
    return cv2.imdecode(image_array, cv2.IMREAD_COLOR)


def _extract_ocr_text(result):
    """从OCR结果中提取文本"""
    if result is None:
        return []
    
    try:
        return dict(list(result)[0])["rec_texts"]
    except Exception as e:
        print(f"未识别到文字: {e}")
        return []


def ocr_form_url_image(url: str) -> list:
    """识别网络图片中的文字"""
    try:
        img = _download_image(url)
        ocr_result = ocr.ocr(img)
        return _extract_ocr_text(ocr_result)
    except Exception as e:
        print(f"下载或识别失败: {e}")
        return []