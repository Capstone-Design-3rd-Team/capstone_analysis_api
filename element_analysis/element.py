import torch
import torchvision
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import os
from dataclasses import dataclass
from typing import Dict, Tuple
import numpy as np
import pytesseract

# 상수
TESSERACT_PATH = '/usr/bin/tesseract'
MIN_CONFIDENCE = 60
CONFIDENCE_THRESHOLD = 0.5

# 모델 체크포인트 경로
MODEL_PATH = "/app/checkpoints/screenrecognition-web350k-vins.torchscript"

# 저장 경로 통일
OUTPUT_DIR = os.path.join(os.getcwd(), "tmp", "file")
os.makedirs(OUTPUT_DIR, exist_ok=True)

class UIAnalyzer:
    def __init__(self):
        self.class_mapping = self._load_class_mapping()
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
        self.contrast_index = 0
        self.button_elements = []

    def _load_class_mapping(self) -> Dict[int, str]:
        """Load VINS class mapping"""
        return {
            0: "BACKGROUND", 1: "OTHER", 2: "Background Image",
            3: "Checked View", 4: "Icon", 5: "Input Field", 6: "Image",
            7: "Text", 8: "Text Button", 9: "Page Indicator",
            10: "Pop-Up Window", 11: "Sliding Menu", 12: "Switch"
        }

    @staticmethod
    def load_and_preprocess_image(image_path: str) -> Tuple[torch.Tensor, Image.Image]:
        """Load and preprocess image"""
        image = Image.open(image_path)
        if image.mode != 'RGB':
            image = image.convert('RGB')
        transform = torchvision.transforms.ToTensor()
        return transform(image), image


    def detect_ui_elements(self, image_path: str) -> None:
        """Detect and analyze UI elements in the image"""
        self.BUTTON_COUNT = 0
        BUTTON_LABELS = [3, 4, 5, 8, 12]  # Checked View, Icon, Input Field, Text Button, Switch
        
        # 모델 로드
        model = torch.jit.load(MODEL_PATH, map_location=torch.device('cpu'))
        model.eval()

        # 모델로드 및 이미지 경로지정
        image_tensor, original_image = self.load_and_preprocess_image(image_path)
        original_image_np = np.array(original_image)

        with torch.no_grad():
            losses, detections = model([image_tensor])
            
            fig, ax = plt.subplots(1, figsize=(10, 10))
            ax.imshow(original_image)

            for i in range(min(50, len(detections[0]['boxes']))):
                if detections[0]['scores'][i] > CONFIDENCE_THRESHOLD:
                    element_data = {
                        'box': detections[0]['boxes'][i].numpy(),
                        'score': detections[0]['scores'][i].item(),
                        'label': detections[0]['labels'][i].item()
                    }
                    
                    if element_data['label'] in BUTTON_LABELS:
                        self.BUTTON_COUNT += 1
                    
                    rect = patches.Rectangle(
                        (element_data['box'][0], element_data['box'][1]),
                        element_data['box'][2] - element_data['box'][0],
                        element_data['box'][3] - element_data['box'][1],
                        linewidth=2, edgecolor='r', facecolor='none'
                    )
                    ax.add_patch(rect)
                    
                    class_name = self.class_mapping.get(element_data['label'],
                                                      f"unknown-{element_data['label']}")
                    ax.text(
                        element_data['box'][0],
                        element_data['box'][1] - 5,
                        f"{class_name}: {element_data['score']:.2f}",
                        color='white',
                        fontsize=12,
                        bbox=dict(facecolor='red', alpha=0.5)
                    )            
            plt.axis('off')
            plt.title("UI Element Detection - VINS")
            plt.savefig(os.path.join(OUTPUT_DIR, "detection_result.png"))
            plt.close()

def main():
    image_path = os.path.join(OUTPUT_DIR, "screenshot.png")  # 스크린샷 경로
    analyzer = UIAnalyzer()
    analyzer.detect_ui_elements(image_path)

if __name__ == "__main__":
    main() 