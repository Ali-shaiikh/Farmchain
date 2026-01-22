#!/usr/bin/env python3
import sys
import os

try:
    import pytesseract
    from pytesseract import Output
    from PIL import Image, ImageEnhance, ImageOps
    import numpy as np
    import re
except ImportError:
    print("Error: Please install pytesseract, Pillow, and numpy: pip install pytesseract pillow numpy", file=sys.stderr)
    print("Also install Tesseract OCR: https://github.com/tesseract-ocr/tesseract", file=sys.stderr)
    sys.exit(1)

# 🧪 TEMPORARY: Reusable OCR function for testing
def preprocess_image_for_ocr(image):
    """
    Preprocess image to improve OCR accuracy on lab reports and structured documents.
    Uses conservative enhancements to avoid over-processing artifacts.
    
    Args:
        image: PIL Image object
    
    Returns:
        Preprocessed PIL Image object
    """
    try:
        # Convert to RGB if necessary
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # CONSERVATIVE approach: minimal preprocessing to avoid artifacts
        # 1. Slight contrast boost (1.5x instead of aggressive 3x)
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(1.5)
        
        # 2. Slight sharpness (1.3x instead of 3x)
        enhancer = ImageEnhance.Sharpness(image)
        image = enhancer.enhance(1.3)
        
        # 3. Very slight brightness adjustment
        enhancer = ImageEnhance.Brightness(image)
        image = enhancer.enhance(1.1)
        
        # 4. Moderate upscaling only if image is very small
        if image.size[0] < 800:
            scale_factor = 1.5
            new_size = (int(image.size[0] * scale_factor), int(image.size[1] * scale_factor))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        return image
    except Exception as e:
        print(f"Warning: Image preprocessing failed: {str(e)}", file=sys.stderr)
        print("Proceeding with original image", file=sys.stderr)
        return image
    
    
def extract_structured_lines(image) -> str:
    """
    Reconstruct lines using Tesseract's image_to_data output.
    Groups tokens by block/paragraph/line and orders by x position.
    Filters low-confidence tokens to reduce noise.
    """
    try:
        data = pytesseract.image_to_data(
            image,
            lang='eng',
            config='--oem 1 --psm 6',
            output_type=Output.DICT,
        )
        n = len(data.get('text', []))
        if n == 0:
            return ""

        lines = {}
        for i in range(n):
            txt = data['text'][i]
            conf_str = str(data['conf'][i])
            try:
                conf = int(float(conf_str))
            except Exception:
                conf = -1
            if conf < 45 or not txt or not txt.strip():
                continue
            key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            if key not in lines:
                lines[key] = []
            lines[key].append((data['left'][i], txt))

        line_texts = []
        for key in sorted(lines.keys()):
            tokens = sorted(lines[key], key=lambda t: t[0])
            line = ' '.join(tok for _, tok in tokens)
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                line_texts.append(line)

        structured = "\n".join(line_texts)
        return structured
    except Exception as e:
        print(f"Warning: Structured OCR failed: {str(e)}", file=sys.stderr)
        return ""
def extract_text_from_image(image_path):
    """
    Extract text from image using OCR with minimal preprocessing and structured fallback.
    """
    try:
        image = Image.open(image_path)
        original_size = image.size

        # Raw OCR (no preprocessing) and structured extraction
        try:
            text_raw = pytesseract.image_to_string(image, lang='eng', config='--oem 1 --psm 3')
        except Exception:
            text_raw = ""
        structured_raw = extract_structured_lines(image)

        # Light preprocessing if both are weak
        preprocessing_used = "NONE (raw image)"
        processed_image = None
        text_proc = ""
        structured_proc = ""
        if len(text_raw.strip()) < 150 and len(structured_raw.strip()) < 150:
            processed_image = preprocess_image_for_ocr(image)
            try:
                text_proc = pytesseract.image_to_string(processed_image, lang='eng', config='--oem 1 --psm 3')
            except Exception:
                text_proc = ""
            structured_proc = extract_structured_lines(processed_image)
            preprocessing_used = "Light preprocessing"

        # Choose best and merge
        candidates = [
            ("raw_text", text_raw),
            ("raw_structured", structured_raw),
            ("proc_text", text_proc),
            ("proc_structured", structured_proc),
        ]

        def soil_hits(t: str) -> int:
            t_low = t.lower()
            keys = ["ph", "nitrogen", "phosphorus", "potassium", "organic", "oc", "kg/ha", "kg ha", "%"]
            return sum(1 for k in keys if k in t_low)

        candidates_sorted = sorted(candidates, key=lambda kv: (soil_hits(kv[1]), len(kv[1].strip())), reverse=True)
        merged_parts = []
        seen_hashes = set()
        for name, content in candidates_sorted:
            c = content.strip()
            if not c:
                continue
            h = hash(c[:1000])
            if h in seen_hashes:
                continue
            merged_parts.append(c)
            seen_hashes.add(h)
            if len(merged_parts) >= 3:
                break

        text_final = "\n".join(merged_parts) if merged_parts else (text_raw or structured_raw or text_proc or structured_proc)

        if len(text_final.strip()) < 50:
            print("⚠️  WARNING: OCR extraction returned insufficient text. Image quality may be poor.", file=sys.stderr)
            print("Try uploading a clearer, higher resolution image of the lab report.", file=sys.stderr)

        return text_final

    except Exception as e:
        print(f"Error extracting text from image: {str(e)}", file=sys.stderr)
        raise
    


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 extract_image.py <image_file_path>", file=sys.stderr)
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    try:
        text = extract_text_from_image(file_path)
        print(text)
    except Exception as e:
        sys.exit(1)
