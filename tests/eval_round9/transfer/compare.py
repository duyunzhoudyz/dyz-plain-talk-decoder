"""主持人提供给评审的匿名题面和首答；票序不代表版本先后。"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent
ORDER = {'N1': ('new', 'old'), 'N2': ('old', 'new'), 'N3': ('new', 'old')}


def load(name):
    return json.loads((ROOT / (name + '.json')).read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'AB', 'BA'}:
        sys.exit('用法：python compare.py AB 或 BA')
    sys.stdout.reconfigure(encoding='utf-8')
    cases = load('protocol')['cases']
    for qid, arms in ORDER.items():
        answers = {label: load(arm)[qid] for label, arm in zip('AB', arms)}
        print(qid, cases[qid]['request'], cases[qid]['text'], sep='\n')
        for label in sys.argv[1]:
            print(label, answers[label], sep='\n')
