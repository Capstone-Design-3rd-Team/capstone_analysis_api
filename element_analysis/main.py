import os
import json
from datetime import datetime
from element import UIAnalyzer
from crawl import WebAnalyzer
import sys
import requests
import boto3



def send_results_to_backend(results, backend_url=None, task_id=None):
    if not backend_url:
        print("[INFO] 백엔드 주소가 없어 전송을 생략합니다.")
        return

    if not task_id:
        print("[ERROR] task_id가 없어 결과를 전송할 수 없습니다.")
        return

    payload = {
        "task_id": task_id,
        "results": results
    }

    try:
        response = requests.post(
            backend_url,
            json=payload,  # 반드시 JSON으로
            timeout=30
        )
        print(f"[INFO] 백엔드 응답 코드: {response.status_code}")
        print(f"[INFO] 백엔드 응답: {response.text}")
    except Exception as e:
        print(f"[ERROR] 백엔드 전송 실패: {e}")




def calculate_score(button_detection_score, button_visual_score, button_size_score, button_contrast_score, font_size_score, overall_contrast_score, korean_ratio_score):
    # 1. 버튼 탐지도 & 버튼 시각적 피드백 (35%)
    button_score = (button_detection_score * 0.8 + button_visual_score * 0.2) * 0.25
    
    # 2. 버튼 크기 & 버튼 명암 대비 (10%)
    button_style_score = (button_size_score * 0.5 + button_contrast_score * 0.5) * 0.05
    
    # 3. 폰트 크기 & 전체 명암 대비 (25%)
    text_score = (font_size_score * 0.5 + overall_contrast_score * 0.5) * 0.4
    
    # 4. 한국어 비율 (30%)
    korean_score = korean_ratio_score * 0.3
    
    return button_score + button_style_score + text_score + korean_score

def get_category_level(score):
    if score < 20:
        return "(심각)"
    elif score < 30:
        return "(보통)"
    else:
        return "(양호)"

def get_severity_level(score):
    if score < 20:
        return "심각"
    elif score < 30:
        return "보통"
    else:
        return "양호"

def get_severity_color(score):
    if score < 20:
        return "red"
    elif score < 30:
        return "orange"
    else:
        return "green"

def get_accessibility_level(score):
    if score >= 50:
        return "매우 우수 (A등급)"
    elif score >= 40:
        return "우수 (B등급)"
    elif score >= 30:
        return "보통 (C등급)"
    else:
        return "미흡 (D등급)"

def save_results_to_json(results, filename="accessibility_analysis_results.json"):
    """분석 결과를 JSON 파일로 저장"""
    try:
        os.makedirs("results", exist_ok=True)
        
        filepath = os.path.join("results", filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"\n분석 결과가 '{filepath}'에 저장되었습니다.")
        return filepath
    except Exception as e:
        print(f"JSON 저장 중 오류 발생: {e}")
        return None

def run_analysis(url, backend_url=None, task_id=None, website_id=None):
    start_time = datetime.now()  # 시작 시간 기록
    print("크롤링 시작...")
    crawler = WebAnalyzer()
    try:
        crawler.analyze(url)
        
        # 2. 스크린샷 분석 실행
        print("\n스크린샷 분석 시작...")

        analyzer = UIAnalyzer()
        screenshot_path = os.path.join(os.getcwd(), "tmp", "file","screenshot.png")

        analyzer.detect_ui_elements(screenshot_path)
        print("스크린샷 분석 완료")

        # ✅ S3 업로드
        s3_url = None 
        s3 = boto3.client('s3')
        bucket_name = "s3-bucket-934029856517-20251029"
        s3_key = f"screenshots/{datetime.now().strftime('%Y%m%d_%H%M%S')}_detection_result.png"

        try:
            s3.upload_file("tmp/file/detection_result.png", bucket_name, s3_key)
            s3_url = f"https://{bucket_name}.s3.ap-northeast-2.amazonaws.com/{s3_key}"
            print(f"[INFO] S3 업로드 완료: {s3_url}")
        except Exception as e:
            print(f"[ERROR] S3 업로드 실패: {e}")
            s3_url = None



        vertical_scroll = crawler.vscroll
        horizontal_scroll = crawler.hscroll
        # 3. 버튼 개수 차이 계산 (버튼 탐지도)
        crawl_button_count = crawler.TOTAL_BUTTON_COUNT
        element_button_count = analyzer.BUTTON_COUNT
        button_count_diff = abs(crawl_button_count - element_button_count)
        if crawl_button_count > 0:
            if crawl_button_count == 0 :
                button_detection_score = 100
            else :
                button_detection_score = max(0, (1 - button_count_diff / crawl_button_count) * 100)
        else:
            button_detection_score = 0
        
         # 4. 각종 점수 계산
        button_visual_score = crawler.get_button_visual_feedback_score()
        button_size_score = crawler.get_button_size_score()
        button_contrast_score = crawler.get_button_contrast_score()
        font_size_score = crawler.get_font_size_score()
        overall_contrast_score = crawler.get_overall_contrast_score()
        korean_ratio_score = crawler.KOREAN_TEXT_RATIO_SCORE  # 직접 속성 접근
        
        # 종합점수 계산
        final_score = calculate_score(
            button_detection_score,
            button_visual_score,
            button_size_score,
            button_contrast_score,
            font_size_score,
            overall_contrast_score,
            korean_ratio_score
        )
        
        # 문제가 있는 항목만 필터링
        issues = []
        if button_detection_score < 30:
            issues.append(("버튼 탐지도", button_detection_score))
        if button_visual_score < 30:
            issues.append(("버튼 시각적 피드백", button_visual_score))
        if button_size_score < 30:
            issues.append(("버튼 크기", button_size_score))
        if button_contrast_score < 30:
            issues.append(("버튼 명암 대비", button_contrast_score))
        if font_size_score < 30:
            issues.append(("폰트 크기", font_size_score))
        if overall_contrast_score < 30:
            issues.append(("전체 명암 대비", overall_contrast_score))
        if korean_ratio_score < 30:
            issues.append(("한국어 비율", korean_ratio_score))
        
        # JSON 결과 구성
        results = {
            "analysis_info": {
                "url": url,
                "analysis_date": datetime.now().isoformat(),
                "screenshot_path": screenshot_path,
                "s3_url": s3_url,  # ✅ 업로드된 이미지 S3 URL
                "task_id" : task_id,
                "website_id": website_id  # 새로 추가

            },
            "scroll_info":{
                "vertical_scroll" : vertical_scroll,
                "horizontal_scroll" : horizontal_scroll
            },
            "button_analysis": {
                "crawled_button_count": crawl_button_count,
                "detected_button_count": element_button_count,
                "button_count_difference": button_count_diff
            },
            "detailed_scores": {
                "button_detection": {
                    "score": round(button_detection_score, 2),
                    "level": get_severity_level(button_detection_score),
                    "color": get_severity_color(button_detection_score),
                    "weight": "20%",
                    "description": "크롤링된 버튼과 실제 탐지된 버튼의 일치도"
                },
                "button_visual_feedback": {
                    "score": round(button_visual_score, 2),
                    "level": get_severity_level(button_visual_score),
                    "color": get_severity_color(button_visual_score),
                    "weight": "5%",
                    "description": "버튼의 시각적 피드백 제공 정도"
                },
                "button_size": {
                    "score": round(button_size_score, 2),
                    "level": get_severity_level(button_size_score),
                    "color": get_severity_color(button_size_score),
                    "weight": "2.5%",
                    "description": "버튼 크기의 접근성 준수 정도"
                },
                "button_contrast": {
                    "score": round(button_contrast_score, 2),
                    "level": get_severity_level(button_contrast_score),
                    "color": get_severity_color(button_contrast_score),
                    "weight": "2.5%",
                    "description": "버튼의 명암 대비"
                },
                "font_size": {
                    "score": round(font_size_score, 2),
                    "level": get_severity_level(font_size_score),
                    "color": get_severity_color(font_size_score),
                    "weight": "20%",
                    "description": "텍스트 폰트 크기의 적절성"
                },
                "overall_contrast": {
                    "score": round(overall_contrast_score, 2),
                    "level": get_severity_level(overall_contrast_score),
                    "color": get_severity_color(overall_contrast_score),
                    "weight": "20%",
                    "description": "전체적인 명암 대비"
                },
                "korean_ratio": {
                    "score": round(korean_ratio_score, 2),
                    "level": get_severity_level(korean_ratio_score),
                    "color": get_severity_color(korean_ratio_score),
                    "weight": "30%",
                    "description": "한국어 텍스트 비율"
                }
            },
            "summary": {
                "final_score": round(final_score, 2),
                "accessibility_level": get_accessibility_level(final_score),
                "severity_level": get_severity_level(final_score),
                "color": get_severity_color(final_score)
            },
            "issues": [
                {
                    "category": issue[0],
                    "score": round(issue[1], 2),
                    "level": get_severity_level(issue[1]),
                    "color": get_severity_color(issue[1])
                }
                for issue in issues
            ],
            "recommendations": generate_recommendations(issues, final_score)
        }
        
        # JSON 파일로 저장
        filename = "result.json"
        save_results_to_json(results, filename)
        send_results_to_backend(results, backend_url=backend_url,task_id=task_id)        
        print_summary(results)
        return results
    
    finally:
        end_time = datetime.now()  # 종료 시간 기록
        elapsed = end_time - start_time
        print(f"[INFO] 분석 종료: {end_time}")
        print(f"[INFO] 총 실행 시간: {elapsed}")

        
def generate_recommendations(issues, final_score):
    """문제점에 따른 개선 권고사항 생성"""
    recommendations = []
    
    issue_dict = {issue[0]: issue[1] for issue in issues}
    
    if "버튼 탐지도" in issue_dict:
        recommendations.append({
            "category": "버튼 탐지도",
            "priority": "높음",
            "recommendation": "버튼 요소에 적절한 HTML 태그(button, input type='button')를 사용하고, role='button' 속성을 추가하세요."
        })
    
    if "버튼 시각적 피드백" in issue_dict:
        recommendations.append({
            "category": "버튼 시각적 피드백",
            "priority": "중간",
            "recommendation": "버튼에 hover, focus, active 상태의 시각적 변화를 추가하세요."
        })
    
    if "버튼 크기" in issue_dict:
        recommendations.append({
            "category": "버튼 크기",
            "priority": "높음",
            "recommendation": "버튼 최소 크기를 44x44px 이상으로 설정하세요."
        })
    
    if "버튼 명암 대비" in issue_dict or "전체 명암 대비" in issue_dict:
        recommendations.append({
            "category": "명암 대비",
            "priority": "높음",
            "recommendation": "텍스트와 배경의 명암 대비를 4.5:1 이상으로 설정하세요."
        })
    
    if "폰트 크기" in issue_dict:
        recommendations.append({
            "category": "폰트 크기",
            "priority": "중간",
            "recommendation": "본문 텍스트는 최소 16px 이상으로 설정하세요."
        })
    
    if "한국어 비율" in issue_dict:
        recommendations.append({
            "category": "한국어 지원",
            "priority": "높음",
            "recommendation": "주요 콘텐츠와 UI 요소를 한국어로 제공하세요."
        })
    
    # 전체 점수에 따른 일반적 권고사항
    if final_score < 50:
        recommendations.append({
            "category": "전반적 개선",
            "priority": "매우 높음",
            "recommendation": "웹 접근성 가이드라인(KWCAG 2.1)을 참고하여 전반적인 개선이 필요합니다."
        })
    
    return recommendations

def print_summary(results):
    """분석 결과 요약 출력"""
    print("\n" + "="*50)
    print("웹 접근성 분석 결과")
    print("="*50)
    
    summary = results["summary"]
    print(f"종합 점수: {summary['final_score']}/100")
    print(f"접근성 등급: {summary['accessibility_level']}")
    print(f"심각도: {summary['severity_level']}")
    
    print(f"\n주요 포인트 ({len(results['issues'])}개):")
    for issue in results["issues"]:
        print(f"  - {issue['category']}: {issue['score']}/100 ({issue['level']})")
    
    print(f"\n개선 권고사항 ({len(results['recommendations'])}개):")
    for rec in results["recommendations"]:
        print(f"  [{rec['priority']}] {rec['category']}: {rec['recommendation']}")

if __name__ == "__main__":
    # argv에서 task_id 무조건 가져오기
    if len(sys.argv) < 4:
        print("[ERROR] 필수 인자가 부족합니다. url, callback_url, task_id 필요")
        sys.exit(1)

    url = sys.argv[1]
    callback_url = sys.argv[2]
    task_id = sys.argv[3]
    website_id = sys.argv[4] if len(sys.argv) >= 5 else None

    print(f"[INFO] URL: {url}")
    print(f"[INFO] Callback URL: {callback_url}")
    print(f"[INFO] task_id ID: {task_id}")
    print(f"[INFO] Website ID: {website_id}")

    run_analysis(url, backend_url=callback_url, task_id=task_id, website_id=website_id)
