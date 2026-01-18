#!/usr/bin/env python3
import sys
import os

try:
    import pytesseract
    from PIL import Image
except ImportError:
    print("Error: Please install pytesseract and Pillow: pip install pytesseract pillow", file=sys.stderr)
    print("Also install Tesseract OCR: https://github.com/tesseract-ocr/tesseract", file=sys.stderr)
    sys.exit(1)

# 🧪 TEMPORARY: Reusable OCR function for testing
def extract_text_from_image(image_path):
    """
    Extract text from image using OCR.
    🧪 TEMPORARY: Includes debug output for OCR testing.
    
    Args:
        image_path: Path to image file
    
    Returns:
        Extracted text string
    """
    try:
        image = Image.open(image_path)
        try:
            text = pytesseract.image_to_string(image, lang='eng+deu')
        except:
            text = pytesseract.image_to_string(image, lang='eng')
        
        # 🧪 TESTING HOOK: Debug output (TEMPORARY - EXACT FORMAT)
        print("----------------------------------------", file=sys.stderr)
        print("FILE TYPE: IMAGE", file=sys.stderr)
        print(f"IMAGE SIZE: {image.size}", file=sys.stderr)
        print(f"EXTRACTED TEXT LENGTH: {len(text)}", file=sys.stderr)
        print("EXTRACTED TEXT (first 1500 chars):", file=sys.stderr)
        print(text[:1500] if text else "[EMPTY - OCR extraction may have failed]", file=sys.stderr)
        print("----------------------------------------", file=sys.stderr)
        
        # 🧪 TESTING HOOK: Warn if extraction is empty
        if len(text.strip()) < 50:
            print("⚠️  WARNING: OCR extraction returned insufficient text. Image quality may be poor.", file=sys.stderr)
        
        return text
    
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
