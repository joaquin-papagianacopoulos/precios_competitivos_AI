import pdfplumber
import openpyxl
import re

def clean_number(value: str) -> str:
    """
    Normaliza los números:
    - Elimina separadores de miles (.)
    - Elimina espacios internos
    - Mantiene coma como separador decimal
    """
    if not isinstance(value, str):
        return value
    
    # Quitar espacios
    value = value.replace(" ", "")
    
    # Quitar puntos si son separadores de miles
    value = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", "", value)
    
    return value

def pdf_to_xlsx(pdf_path: str, xlsx_path: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PDF Data"

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    cleaned_row = []
                    for i, cell in enumerate(row):
                        if cell is None:
                            cleaned_row.append("")
                            continue
                        
                        # Suponemos que la primera columna es "producto"
                        if i == 0:
                            cleaned_row.append(cell.strip())  # Mantiene espacios internos
                        else:
                            cleaned_row.append(clean_number(cell))
                    
                    ws.append(cleaned_row)
                ws.append([])  # Separador de tablas

    wb.save(xlsx_path)
    print(f"✅ Archivo convertido: {xlsx_path}")


if __name__ == "__main__":
    pdf_file = "candy.pdf"   # Cambiar por tu PDF
    xlsx_file = "salida.xlsx"
    pdf_to_xlsx(pdf_file, xlsx_file)
