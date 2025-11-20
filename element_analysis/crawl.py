# -*- coding: utf-8 -*-
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from collections import defaultdict
import re
import time
import os
import cairosvg
import pytesseract
from PIL import Image
import io
import atexit
import signal
import sys
import shutil

# --- 외부 도구 경로 (환경에 맞게 조정 가능) ---
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium"
]
CHROMEDRIVER_PATH = "/usr/bin/chromedriver"


class WebAnalyzer:
    def __init__(self, enable_svg_ocr: bool = False):
        # 상태/임시자원
        self.driver = None
        self.temp_dirs = []
        self.temp_files = []
        self.setup_signal_handlers()
        self.setup_cleanup()
        self.setup_directories()

        # 분석 상태
        self.style_groups = defaultdict(list)
        self.processed_elements = set()
        self.analysis_results = {}
        self.button_elements = []
        self.page_buttons = []
        self.TOTAL_BUTTON_COUNT = 0
        self.korean_ratio = 0.0
        self.vscroll = False
        self.hscroll = False

        # 기준
        self.min_contrast = 4.5       # WCAG AA
        self.min_text_size_px = 16
        self.min_button_size = 44

        # 옵션
        self.enable_svg_ocr = enable_svg_ocr

        # WebDriver
        self.driver = self.setup_driver()
        self.apply_cdp_blocking_and_css()  # 리소스 차단 + 전역 CSS 주입

    # ----------------------------- 공용 유틸 -----------------------------
    def setup_signal_handlers(self):
        def signal_handler(signum, frame):
            print(f"\n신호 {signum} 수신. 정리 작업 진행 중...")
            self.cleanup_all()
            sys.exit(0)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def setup_cleanup(self):
        atexit.register(self.cleanup_all)

    def cleanup_all(self):
        """드라이버/임시파일 정리 (명시적 close 혹은 프로세스 종료 시 호출)"""
        try:
            if getattr(self, 'driver', None) and getattr(self.driver, 'session_id', None):
                self.driver.quit()
                print("WebDriver 정상 종료")
        except Exception as e:
            print(f"WebDriver 종료 중 오류: {e}")
        finally:
            self.driver = None

    def close(self):
        """명시적 종료(권장)"""
        for temp_dir in getattr(self, "temp_dirs", []):
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        self.cleanup_all()

    def setup_directories(self):
        try:
            self.work_dir = os.path.join(os.getcwd(), "tmp", "file")
            os.makedirs(self.work_dir, exist_ok=True)
            self.output_dir = self.work_dir
            print(f"작업 디렉토리 생성/확인: {self.work_dir}")
        except Exception as e:
            print(f"디렉토리 설정 실패: {e}")
            raise

    # ----------------------------- 브라우저 세팅 -----------------------------
    def setup_driver(self):
        try:
            options = Options()
            options.page_load_strategy = 'eager'  # DOMContentLoaded 기준

            # 안정/성능 옵션
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-features=VizDisplayCompositor")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
            options.add_argument("--disable-background-timer-throttling")
            options.add_argument("--disable-backgrounding-occluded-windows")
            options.add_argument("--disable-renderer-backgrounding")
            options.add_argument("--log-level=3")
            options.add_argument("--no-zygote")
            options.add_argument("--disable-background-networking")

            # 캡처 선명도
            options.add_argument("--high-dpi-support=2")
            options.add_argument("--force-device-scale-factor=2")

            # 프로필
            self.user_data_dir = os.path.join(self.work_dir, "chrome_temp_profile")
            os.makedirs(self.user_data_dir, exist_ok=True)
            options.add_argument(f"--user-data-dir={self.user_data_dir}")

            # 콘텐츠 설정(이미지는 CDP로 차단하므로 여기선 최소화)
            prefs = {"profile.default_content_setting_values": {
                "notifications": 2, "media_stream": 2, "geolocation": 2, "popups": 2
            }}
            options.add_experimental_option("prefs", prefs)

            # 모바일 에뮬로 정의
            mobile_emulation = {
                "deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 3.0},
                "userAgent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
            }
            options.add_experimental_option("mobileEmulation", mobile_emulation)

            # 바이너리/드라이버
            chrome_path = next((p for p in CHROME_CANDIDATES if p and os.path.exists(p)), None)
            if not chrome_path:
                raise FileNotFoundError("Chrome/Chromium 실행 파일을 찾을 수 없습니다")
            options.binary_location = chrome_path
            print(f"Chrome 경로: {chrome_path}")

            if not os.path.exists(CHROMEDRIVER_PATH):
                raise FileNotFoundError(f"ChromeDriver를 찾을 수 없습니다: {CHROMEDRIVER_PATH}")

            service = Service(CHROMEDRIVER_PATH)
            driver = webdriver.Chrome(service=service, options=options)

            # 타임아웃
            driver.set_page_load_timeout(20) 
            driver.set_script_timeout(10)    
            driver.implicitly_wait(5)

            print("WebDriver 초기화 완료")
            return driver

        except Exception as e:
            print(f"WebDriver 초기화 실패: {e}")
            self.cleanup_all()
            raise

    def apply_cdp_blocking_and_css(self):
        """CDP 리소스 차단 + 전역 CSS 주입(애니/트랜지션 제거, 세로 스크롤 금지, 폰트 폴백)"""
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            blocked = [
                "*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif",
                "*.mp4", "*.webm",
                "*.woff", "*.woff2", "*.ttf", "*.otf",
                "*google-analytics*", "*googletagmanager*", "*doubleclick*",
                "*adservice*", "*adsense*", "*ads/*", "*/ads/*",
                "*connect.facebook.net*", "*bat.bing.com*"
            ]
            self.driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": blocked})
            print(f"CDP 차단 패턴 적용: {len(blocked)}개")

            # 문서 생성 시점에 스타일 주입
            inject_js = r"""
              (function() {
                try {
                  const style = document.createElement('style');
                  style.setAttribute('data-wa-hardening', 'true');
                  style.textContent = `
                    * { animation: none !important; transition: none !important; }
                    html, body { overflow-y: hidden !important; overscroll-behavior: none !important; }
                    html, body { scroll-behavior: auto !important; }
                    body, *:not(i):not(svg) {
                      font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI",
                                   Roboto, "Helvetica Neue", Arial, "Apple SD Gothic Neo",
                                   "Noto Sans KR", "Malgun Gothic", sans-serif !important;
                    }
                  `;
                  document.documentElement.appendChild(style);
                } catch (e) {}
              })();
            """
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": inject_js})
            print("전역 CSS/JS 주입 준비 완료 (애니메이션 차단 + 스크롤 금지 + 폰트 폴백)")
        except Exception as e:
            print(f"CDP 차단/주입 세팅 실패: {e}")

    def take_full_screenshot(self):
        try:
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            screenshot_path = os.path.join(self.output_dir, "screenshot.png")
            total_height = self.driver.execute_script(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) || 812"
            )
            max_height = 12000
            target_height = min(total_height, max_height)
            self.driver.set_window_size(375, target_height)
            if self.driver.save_screenshot(screenshot_path):
                print(f"스크린샷 저장 완료: {screenshot_path}")
                return screenshot_path
            print("스크린샷 저장 실패")
            return None
        except TimeoutException:
            print("페이지 로딩 타임아웃")
            return None
        except Exception as e:
            print(f"스크린샷 저장 실패: {e}")
            return None

    def save_page_content(self):
        try:
            html = self.driver.page_source
            html_path = os.path.join(self.output_dir, "page.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            self.temp_files.append(html_path)
            print("HTML 저장 완료")

            # 외부 CSS 시도(6s 타임아웃). 차단으로 실패 가능 → 무시
            css_links = []
            try:
                link_elements = self.driver.find_elements(By.CSS_SELECTOR, 'link[rel="stylesheet"]')
                css_links = [link.get_attribute('href') for link in link_elements if link.get_attribute('href')]
            except Exception as e:
                print(f"CSS 링크 수집 실패: {e}")

            sess = requests.Session()
            for i, link in enumerate(css_links):
                try:
                    resp = sess.get(link, timeout=6)
                    resp.encoding = resp.apparent_encoding
                    css_path = os.path.join(self.output_dir, f"style_{i+1}.css")
                    with open(css_path, "w", encoding="utf-8") as f:
                        f.write(resp.text)
                    self.temp_files.append(css_path)
                    print(f"CSS {i+1} 다운로드 완료")
                except Exception as e:
                    print(f"CSS {i+1} 다운로드 실패: {e}")
        except Exception as e:
            print(f"페이지 콘텐츠 저장 실패: {e}")

    def safe_execute_script(self, script, *args):
        try:
            return self.driver.execute_script(script, *args)
        except WebDriverException as e:
            print(f"JavaScript 실행 실패: {e}")
            return None
        except Exception as e:
            print(f"스크립트 실행 중 오류: {e}")
            return None

    def find_pagination_buttons(self):
        self.page_buttons = []
        selectors = [
            "button", "[role='button']", "a[href]", "[onclick]",
            "[class*='btn']", "[class*='button']", "[id*='btn']", "[id*='button']",
            "span[onclick]", "div[onclick]", "[style*='cursor:pointer']", "[style*='cursor: pointer']"
        ]
        candidates = []
        for sel in selectors:
            try:
                candidates.extend(self.driver.find_elements(By.CSS_SELECTOR, sel))
            except Exception as e:
                print(f"선택자 '{sel}' 처리 실패: {e}")
        candidates = list(set(candidates))
        print(f"페이지 버튼 후보: {len(candidates)}개")

        for el in candidates:
            try:
                if not self.is_visible(el):
                    continue
                text = (el.text or "").strip()
                if not text:
                    text = (el.get_attribute("aria-label") or el.get_attribute("title") or el.get_attribute("value") or "").strip()
                if text.isdigit() or text in ['◀','▶','<','>','이전','다음','prev','next']:
                    # 클릭 가능성 판정 (휴리스틱)
                    has_click = bool(el.get_attribute("onclick"))
                    if not has_click:
                        tag = el.tag_name.lower()
                        role = (el.get_attribute("role") or "").lower()
                        href = el.get_attribute("href")
                        cursor = (self.safe_execute_script("return window.getComputedStyle(arguments[0]).cursor;", el) or "").lower()
                        has_click = (
                            (tag in ("a", "button", "input") and (href or tag == "button")) or
                            (role == "button") or
                            ("pointer" in cursor)
                        )
                    self.page_buttons.append({"element": el, "text": text, "has_click_event": has_click})
            except StaleElementReferenceException:
                continue
            except Exception as e:
                print(f"페이지 버튼 분석 중 오류: {e}")
        self.TOTAL_BUTTON_COUNT = len(self.page_buttons)
        print(f"페이지 버튼 탐지 완료: {self.TOTAL_BUTTON_COUNT}개")

    def is_processed_child(self, el):
        """이미 처리된 요소의 하위인지 확인하여 중복 분석 방지"""
        try:
            parent = el
            while parent:
                if parent in self.processed_elements:
                    return True
                parent = self.driver.execute_script("return arguments[0].parentElement;", parent)
            return False
        except Exception:
            return False

    def has_scrollbar(self):
        try:
            sh = self.driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
            ch = self.driver.execute_script("return window.innerHeight")
            sw = self.driver.execute_script("return Math.max(document.body.scrollWidth, document.documentElement.scrollWidth)")
            cw = self.driver.execute_script("return window.innerWidth")
            return (sh or 0) > (ch or 0), (sw or 0) > (cw or 0)
        except Exception as e:
            print(f"스크롤 확인 실패: {e}")
            return False, False

    def get_luminance(self, rgb):
        r, g, b = [x / 255.0 for x in rgb]
        def ch(c): return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

    def contrast_ratio(self, rgb1, rgb2):
        L1, L2 = max(self.get_luminance(rgb1), self.get_luminance(rgb2)), min(self.get_luminance(rgb1), self.get_luminance(rgb2))
        return (L1 + 0.05) / (L2 + 0.05)

    def get_valid_background_color(self, el):
        while el:
            style = self.driver.execute_script("""
                const computed = window.getComputedStyle(arguments[0]);
                return { backgroundColor: computed.backgroundColor };
            """, el)
            bg = style['backgroundColor']
            if bg and not ("rgba(0, 0, 0, 0)" in bg or "transparent" in bg):
                return bg
            el = self.driver.execute_script("return arguments[0].parentElement;", el)
        return "rgb(255, 255, 255)"

    def has_text_child(self, el):
        try:
            children = el.find_elements(By.XPATH, "./*")
            return any((c.text or "").strip() for c in children)
        except Exception:
            return False

    def is_button_like(self, el):
        tag = el.tag_name.lower()
        role = (el.get_attribute("role") or "").lower()
        return (tag == "button") or (role == "button") or (el.get_attribute("onclick") is not None)

    def is_in_viewport(self, el):
        try:
            rect = self.safe_execute_script("""
                const r = arguments[0].getBoundingClientRect();
                return {top:r.top,left:r.left,bottom:r.bottom,right:r.right};
            """, el)
            if not rect:
                return False
            vh = self.safe_execute_script("return window.innerHeight;") or 0
            vw = self.safe_execute_script("return window.innerWidth;") or 0
            return (rect['bottom'] > 0 and rect['right'] > 0 and rect['top'] < vh and rect['left'] < vw)
        except Exception:
            return False

    def is_visible(self, el):
        try:
            style = self.driver.execute_script("""
                const el = arguments[0];
                const c = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return { display:c.display, visibility:c.visibility, opacity:parseFloat(c.opacity),
                         width:r.width, height:r.height };
            """, el)
            return (style['display'] != 'none' and style['visibility'] != 'hidden' and
                    style['opacity'] > 0 and style['width'] > 0 and style['height'] > 0)
        except Exception:
            return False

    # ----------------------------- 배치 수집/분석 -----------------------------
    def get_elements_data_batch(self, elements):
        try:
            return self.driver.execute_script("""
                const elements = arguments[0];
                const results = [];
                for (let i=0;i<elements.length;i++){
                    try {
                        const el = elements[i];
                        const c = window.getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        results.push({
                            index: i,
                            tagName: el.tagName.toLowerCase(),
                            text: el.innerText?.trim() || '',
                            fontSize: c.fontSize,
                            color: c.color,
                            backgroundColor: c.backgroundColor,
                            display: c.display,
                            visibility: c.visibility,
                            opacity: parseFloat(c.opacity),
                            width: r.width,
                            height: r.height,
                            role: el.getAttribute('role'),
                            onclick: el.getAttribute('onclick') !== null,
                            hasSvg: el.querySelectorAll('svg').length > 0,
                            hasImg: el.querySelectorAll('img').length > 0,
                            isVisible: c.display !== 'none' && c.visibility !== 'hidden' && parseFloat(c.opacity) > 0 && r.width>0 && r.height>0
                        });
                    } catch(e){ results.push({index:i, error:e.message}); }
                }
                return results;
            """, elements)
        except Exception as e:
            print(f"배치 데이터 수집 실패: {e}")
            return []

    def process_elements_batch(self, elements):
        print(f"배치 처리 시작: {len(elements)}개 요소")
        elements_data = self.get_elements_data_batch(elements)
        processed, skipped = 0, 0
        for i, data in enumerate(elements_data):
            try:
                if 'error' in data or not data['isVisible']:
                    skipped += 1; continue
                element = elements[data['index']]
                if self.is_processed_child(element):
                    skipped += 1; continue
                self.analyze_element_from_data(element, data)
                processed += 1
                if processed % 100 == 0:
                    print(f"진행률: {processed}/{len(elements)} 처리됨")
            except StaleElementReferenceException:
                skipped += 1
            except Exception as e:
                print(f"요소 [{i}] 처리 실패: {e}")
                skipped += 1
        print(f"배치 처리 완료: {processed}개 처리, {skipped}개 건너뜀")

    def analyze_element_from_data(self, element, data):
        try:
            text = data['text']
            is_button = (data['tagName'] == "button" or data['role'] == "button" or data['onclick'])
            has_text = bool(text)
            has_icon = data['hasSvg'] or data['hasImg']

            # SVG/OCR 지연 평가: 진짜 필요할 때만으로 최적화
            if (not has_text) and is_button and data['hasSvg'] and self.enable_svg_ocr:
                try:
                    svg_html = self.driver.execute_script("""
                        const el = arguments[0];
                        const svg = el.querySelector('svg');
                        return svg ? svg.outerHTML : '';
                    """, element) or ""
                    if svg_html:
                        ocr_text = self.svg_to_text_ocr(svg_html)
                        if ocr_text:
                            text = ocr_text.strip()
                            has_text = True
                except Exception:
                    pass

            has_content = has_text or (is_button and has_icon)
            if not has_content and not is_button:
                return
            if (not is_button) and has_text and self.has_text_child(element):
                return

            font_size = data['fontSize']
            color = data['color']
            bg_color = data['backgroundColor']
            # 투명 배경 보정
            if (("rgba" in bg_color and bg_color.endswith(", 0)")) or ("transparent" in bg_color)):
                bg_color = self.get_valid_background_color(element)

            width, height = data['width'], data['height']
            font_size_px = float(font_size.replace("px", "").strip()) if isinstance(font_size, str) and font_size.endswith("px") else 16.0

            self.processed_elements.add(element)
            key = (font_size, color, bg_color)
            self.style_groups[key].append((element, data['index'], text, is_button, has_icon, width, height, font_size_px))
        except Exception as e:
            print(f"요소 분석 실패: {e}")

    # ----------------------------- 상위 흐름 -----------------------------
    def get_viewport_elements(self):
        try:
            selector = (
                "a,button,input,textarea,select,label,"
                "[role='button'],[onclick],[href],[class*='btn'],[class*='button'],"
                "[id*='btn'],[id*='button'],h1,h2,h3,h4,h5,h6,p,li,span,div"
            )
            elements = self.driver.execute_script("""
                const sel = arguments[0];
                const all = Array.from(document.querySelectorAll(sel));
                const vh = window.innerHeight, vw = window.innerWidth;
                return all.filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    const vis = s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0;
                    const inV = r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
                    return vis && inV;
                });
            """, selector)
            print(f"뷰포트 내 요소 수: {len(elements)}개")
            return elements
        except Exception as e:
            print(f"뷰포트 요소 수집 실패: {e}")
            return []

    def analyze(self, url):
        try:
            self.driver.get(url)
            time.sleep(2)  # 초기 안정화
            v_scroll, h_scroll = self.has_scrollbar()
            self.vscroll, self.hscroll = v_scroll, h_scroll
            print(f"👉 세로 스크롤: {'있음' if v_scroll else '없음'}")
            print(f"👉 가로 스크롤: {'있음' if h_scroll else '없음'}")

            self.analysis_results["scrollbar"] = {"vertical_scroll": v_scroll, "horizontal_scroll": h_scroll}

            self.take_full_screenshot()
            self.save_page_content()
            self.find_pagination_buttons()

            elements = self.get_viewport_elements()
            self.process_elements_batch(elements)

            # 버튼 메타 수집(요약용)
            btn_selectors = [
                "button","[role='button']","input[type='button']","input[type='submit']","input[type='reset']",
                "a[href]","a[onclick]","[class*='btn']","[class*='button']","[id*='btn']","[id*='button']",
                "span[onclick]","div[onclick]","[style*='cursor: pointer']","[style*='cursor:pointer']"
            ]
            combined = ",".join(btn_selectors)
            all_candidates = self.driver.find_elements(By.CSS_SELECTOR, combined)
            viewport_buttons = [el for el in all_candidates if self.is_visible(el) and self.is_in_viewport(el)]

            buttons_data = self.driver.execute_script("""
                return arguments[0].map(el => {
                    const r = el.getBoundingClientRect();
                    const c = window.getComputedStyle(el);
                    return {
                        x: r.x, y: r.y, width: r.width, height: r.height,
                        text: el.innerText?.trim() || el.getAttribute('aria-label') ||
                              el.getAttribute('title') || el.getAttribute('value') ||
                              el.getAttribute('placeholder') || el.getAttribute('href') || '(없음)',
                        background_color: c.backgroundColor, text_color: c.color, cursor: c.cursor,
                        border: c.border, boxShadow: c.boxShadow,
                        element_type: el.tagName.toLowerCase(),
                        role: el.getAttribute('role'), href: el.getAttribute('href'),
                        onclick: el.getAttribute('onclick'), class: el.className
                    };
                });
            """, viewport_buttons)

            self.button_elements = buttons_data
            self.TOTAL_BUTTON_COUNT = len(buttons_data)
            print(f"뷰포트 내에서 {self.TOTAL_BUTTON_COUNT}개의 버튼 요소를 찾았습니다.")

            # 요약에 쓰는 점수(전체 텍스트 기준)
            contrast_scores, font_size_scores = [], []
            for (font_size, color, bg_color), group in self.style_groups.items():
                try:
                    rgb_fg = tuple(map(int, re.findall(r'\d+', color)[:3]))
                    rgb_bg = tuple(map(int, re.findall(r'\d+', bg_color)[:3]))
                    contrast = self.contrast_ratio(rgb_fg, rgb_bg)
                    contrast_scores.append(min(contrast / self.min_contrast, 1.0) * 100)
                    font_px = float(font_size.replace("px", "").strip()) if isinstance(font_size, str) and font_size.endswith("px") else 16.0
                    font_size_scores.append(min(font_px / self.min_text_size_px, 1.0) * 100)
                except Exception:
                    continue

            self.CONTRAST_RATIO_SCORE = sum(contrast_scores) / len(contrast_scores) if contrast_scores else 0
            self.FONT_SIZE_SCORE = sum(font_size_scores) / len(font_size_scores) if font_size_scores else 0
            self.KOREAN_TEXT_RATIO_SCORE = self.calculate_korean_ratio()
            self.finalize_analysis_results()

        except Exception as e:
            print(f"웹페이지 분석 중 오류 발생: {e}")
            raise

    def finalize_analysis_results(self):
        try:
            print("\n=== 분석 결과 정리 ===")
            total_elements = sum(len(group) for group in self.style_groups.values())
            unique_styles = len(self.style_groups)
            print(f"총 분석된 요소: {total_elements}개")
            print(f"고유한 스타일 그룹: {unique_styles}개")
            self.korean_ratio = self.calculate_korean_ratio()
            self.analysis_results.update({
                "total_elements": total_elements,
                "unique_styles": unique_styles,
                "korean_ratio": self.korean_ratio,
                "page_buttons_count": len(self.page_buttons)
            })
            print("분석 결과 정리 완료")
        except Exception as e:
            print(f"결과 정리 중 오류: {e}")

    # ----------------------------- 텍스트/점수 -----------------------------
    def is_korean_text(self, text):
        korean_ranges = [(0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xD7B0, 0xD7FF)]
        korean_count = 0; total_count = 0
        for ch in text:
            if ch.strip() and ch.isalnum():
                total_count += 1
                code = ord(ch)
                for start, end in korean_ranges:
                    if start <= code <= end:
                        korean_count += 1; break
        return korean_count, total_count

    def calculate_korean_ratio(self):
        total_chars = 0; korean_chars = 0
        print("\n=== 한글 비율 계산 시작 ===")
        for (_, _, _), group in self.style_groups.items():
            for el, idx, text, is_button, has_icon, width, height, font_size_px in group:
                if text:
                    kc, tc = self.is_korean_text(text)
                    total_chars += tc; korean_chars += kc
        if not total_chars:
            print("분석할 텍스트가 없습니다.")
            return 0.0
        ratio = (korean_chars / total_chars) * 100
        print(f"한글 문자 수: {korean_chars}")
        print(f"전체 문자 수: {total_chars}")
        print(f"한글 비율: {ratio:.1f}%")
        return ratio

    def svg_to_text_ocr(self, svg_html):
        try:
            png_bytes = cairosvg.svg2png(bytestring=svg_html.encode('utf-8'))
            image = Image.open(io.BytesIO(png_bytes))
            text = pytesseract.image_to_string(image, lang='kor+eng')
            return text.strip()
        except Exception:
            return ""

    # --- 점수 요약 ---
    def count_visual_feedback_changes(self, button):
        changes = [
            button.get('background_change', False),
            button.get('text_change', False),
            button.get('border_change', False),
            button.get('shadow_change', False),
            button.get('transform_change', False),
            button.get('size_change', False),
        ]
        return sum(bool(c) for c in changes)

    def get_button_visual_feedback_score(self):
        if not self.button_elements: return 0
        count = sum(1 for b in self.button_elements if self.count_visual_feedback_changes(b) >= 2)
        return (count / len(self.button_elements)) * 100

    def get_button_size_score(self):
        if not self.button_elements: return 0
        count = sum(1 for b in self.button_elements if b['width'] >= self.min_button_size and b['height'] >= self.min_button_size)
        return (count / len(self.button_elements)) * 100

    def get_button_contrast_score(self):
        if not self.button_elements: return 0
        ok = 0
        for b in self.button_elements:
            try:
                bg_rgb = tuple(map(int, re.findall(r'\d+', b['background_color'])[:3]))
                text_rgb = tuple(map(int, re.findall(r'\d+', b['text_color'])[:3]))
                if self.contrast_ratio(bg_rgb, text_rgb) >= self.min_contrast:
                    ok += 1
            except Exception:
                continue
        return (ok / len(self.button_elements)) * 100

    def get_font_size_score(self):
        total = 0; count = 0
        for (font_size, _, _), group in self.style_groups.items():
            px = float(font_size.replace("px", "").strip()) if isinstance(font_size, str) and font_size.endswith("px") else 16.0
            n = len(group); total += n
            if px >= self.min_text_size_px: count += n
        return (count / total) * 100 if total else 0

    def get_overall_contrast_score(self):
        total = 0; count = 0
        for (font_size, color, bg_color), group in self.style_groups.items():
            try:
                rgb_fg = tuple(map(int, re.findall(r'\d+', color)[:3]))
                rgb_bg = tuple(map(int, re.findall(r'\d+', bg_color)[:3]))
                contrast = self.contrast_ratio(rgb_fg, rgb_bg)
                n = len(group); total += n
                if contrast >= self.min_contrast: count += n
            except Exception:
                continue
        return (count / total) * 100 if total else 0

    def get_analysis_summary(self):
        if not self.analysis_results:
            return "분석이 완료되지 않았습니다."
        summary = f"""
=== 웹 접근성 분석 결과 ===
총 분석된 요소: {self.analysis_results.get('total_elements', 0)}개
고유한 스타일 그룹: {self.analysis_results.get('unique_styles', 0)}개
한글 텍스트 비율: {self.analysis_results.get('korean_ratio', 0):.1f}%
페이지 버튼 수: {self.analysis_results.get('page_buttons_count', 0)}개

스크롤 정보:
- 세로 스크롤: {'있음' if self.analysis_results.get('scrollbar', {}).get('vertical_scroll', False) else '없음'}
- 가로 스크롤: {'있음' if self.analysis_results.get('scrollbar', {}).get('horizontal_scroll', False) else '없음'}

성능 점수:
- 글꼴 크기: {self.get_font_size_score():.1f}%
- 전체 명암 대비: {self.get_overall_contrast_score():.1f}%
- 버튼 크기: {self.get_button_size_score():.1f}%
- 버튼 명암 대비: {self.get_button_contrast_score():.1f}%
- 버튼 시각적 피드백: {self.get_button_visual_feedback_score():.1f}%
        """
        return summary.strip()
    
#테스트
if __name__ == "__main__":
    TEST_URLS = [
        "https://example.com",
        "https://www.wikipedia.org"
    ]

    wa = None
    try:
        wa = WebAnalyzer(enable_svg_ocr=False)
        for url in TEST_URLS:
            print("\n" + "="*80)
            print(f"[TEST] Analyze: {url}")
            print("="*80)
            wa.analyze(url)
            print(wa.get_analysis_summary())
        print("\n[OK] 스모크 테스트 완료")
    except Exception as e:
        print(f"[FAIL] 예외 발생: {e}")
    finally:
        if wa: wa.close()
