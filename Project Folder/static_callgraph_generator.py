import pyan
import glob
import os

PROJECT_PATH = r"C:\Users\Johan\Desktop\ELG-app\Project Folder"

# - "dot" = GraphViz format (kräver GraphViz för att visa)
# - "svg" = SVG bild (kräver GraphViz installerat)
# - "html" = Interaktiv HTML (kräver GraphViz installerat)
FORMAT = "dot"

RANKDIR = "LR" # Riktning: "LR", "TB"
FUNCTION_FILTER = None # Exempel: "my_module.my_function"
NAMESPACE_FILTER = None # Exempel: "meeting_app"
NESTED_GROUPS = True # Visa grupper efter moduler och submoduler
DRAW_DEFINES = True # Rita "defines" edges (funktioner som definieras)
DRAW_USES = True # Rita "uses" edges (funktioner som används/kallas)
COLORED = True # Färglägg grafen
GROUPED_ALT = False # Använd alternativ gruppering
ANNOTATED = False # Annotera med filnamn
GROUPED = True # Gruppera efter moduler

all_files = [f for f in glob.glob(os.path.join(PROJECT_PATH, "**", "*.py"), recursive=True)] # Hitta alla .py-filer i projektmappen och undermappar
print(f"{len(all_files)} Python files found. Generating callgraph...")
callgraph = pyan.create_callgraph(
    filenames=all_files,
    format=FORMAT,
    rankdir=RANKDIR,
    function=FUNCTION_FILTER,
    namespace=NAMESPACE_FILTER,
    nested_groups=NESTED_GROUPS,
    draw_defines=DRAW_DEFINES,
    draw_uses=DRAW_USES,
    colored=COLORED,
    grouped_alt=GROUPED_ALT,
    annotated=ANNOTATED,
    grouped=GROUPED
)
file_extension = FORMAT if FORMAT != "dot" else "dot"
output_path = os.path.join(PROJECT_PATH, f"callgraph.{file_extension}")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(callgraph)
print(f"✓ Call graph saved as {output_path}")