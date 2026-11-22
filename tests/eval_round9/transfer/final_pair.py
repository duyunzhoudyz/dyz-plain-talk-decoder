"""只展示购票题的匿名旧/最终版，用于专项复验；不覆盖第一次对照。"""
import sys

from compare import load


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'AB', 'BA'}:
        sys.exit('用法：python final_pair.py AB 或 BA')
    sys.stdout.reconfigure(encoding='utf-8')
    case = load('protocol')['cases']['N2']
    print(case['request'], case['text'], sep='\n')
    arms = {'A': 'final', 'B': 'old'}
    for label in sys.argv[1]:
        print(label, load(arms[label])['N2'], sep='\n')
