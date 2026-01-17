# File Upload Setup Guide

## ✅ Implementation Complete

### Features Added:
1. **File Upload UI** - PDF and image upload support
2. **Text Extraction** - Server-side PDF parsing and OCR
3. **Text Normalization** - Clean extracted text before sending to Ollama
4. **Auto-fill Textarea** - Extracted text automatically fills the form

## 📦 Required Dependencies

### Node.js (Already installed):
- `multer` - File upload handling ✓

### Python (Install these):
```bash
cd /Users/alishaikh/Desktop/FarmChain/Farmchain
pip install pdfplumber PyPDF2 pytesseract Pillow
```

### System Requirements:
- **Tesseract OCR** (for image OCR):
  ```bash
  # macOS
  brew install tesseract
  
  # Ubuntu/Debian
  sudo apt-get install tesseract-ocr
  
  # Windows
  # Download from: https://github.com/UB-Mannheim/tesseract/wiki
  ```

## 🔄 End-to-End Flow

```
User uploads PDF/Image
        ↓
Backend extracts text (PDF parser / OCR)
        ↓
Text normalized (remove headers, page numbers, etc.)
        ↓
Extracted text auto-fills textarea
        ↓
User reviews/edits text (optional)
        ↓
User selects district, season, irrigation
        ↓
Click "Analyze Soil Report"
        ↓
Ollama Call #1 → Structured JSON (recommendations)
        ↓
Ollama Call #2 → Explanation text
        ↓
UI renders results
```

## 📝 File Structure

- `webapp/routes/soil_ai.js` - File upload endpoint `/soil-ai/extract`
- `extract_pdf.py` - PDF text extraction script
- `extract_image.py` - Image OCR extraction script
- `webapp/views/soil-ai.ejs` - File upload UI + JavaScript handler

## 🧪 Testing

### Test PDF Upload:
1. Upload a soil report PDF
2. Check textarea auto-fills
3. Verify text is clean (no headers/page numbers)
4. Click Analyze
5. Confirm same output as manual paste

### Test Image Upload:
1. Upload phone photo of soil report
2. OCR fills textarea
3. Minor OCR errors are OK (AI can handle)
4. Click Analyze

## ⚠️ Important Notes

- **Ollama NEVER sees files** - Only plain text is sent
- **Textarea always available** - Manual paste fallback maintained
- **Text normalization** - Headers, footers, page numbers removed
- **File size limit** - 10MB maximum
- **Supported formats** - PDF, JPG, JPEG, PNG

## 🔧 Troubleshooting

### PDF extraction fails:
- Install: `pip install pdfplumber` or `pip install PyPDF2`
- Check Python script permissions

### OCR fails:
- Install Tesseract: `brew install tesseract`
- Install Python libs: `pip install pytesseract Pillow`
- Check Tesseract path in system

### File upload fails:
- Check file size (< 10MB)
- Verify file type (.pdf, .jpg, .jpeg, .png)
- Check server logs for errors
