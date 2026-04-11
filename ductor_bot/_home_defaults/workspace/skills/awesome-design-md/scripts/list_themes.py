import os
import json

def list_themes():
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    library_path = os.path.join(base_path, 'library', 'design-md')
    
    if not os.path.exists(library_path):
        return {"error": "Library path not found"}
    
    themes = []
    for item in os.listdir(library_path):
        item_path = os.path.join(library_path, item)
        if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, 'DESIGN.md')):
            themes.append(item)
    
    return sorted(themes)

if __name__ == "__main__":
    themes = list_themes()
    print(json.dumps(themes, indent=2))
