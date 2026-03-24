import os
import json

os.makedirs("data/storage", exist_ok=True)

index_dir = os.path.join("storage", "index")
if os.path.exists(index_dir):
    for file in os.listdir(index_dir):
        if file.endswith(".jsonl"):
            letter = file.split(".")[0]
            
            with open(os.path.join(index_dir, file), "r", encoding="utf-8") as fin:
                with open(os.path.join("data", "storage", f"{letter}.data"), "w", encoding="utf-8") as fout:
                    for line in fin:
                        if not line.strip(): continue
                        try:
                            d = json.loads(line)
                            fout.write(f"{d['term']} {d['relevant_url']} {d['origin_url']} {d['depth']} {d['frequency']}\n")
                        except:
                            continue

print("✅ Quiz format successfully exported! Check the 'data/storage' folder.")