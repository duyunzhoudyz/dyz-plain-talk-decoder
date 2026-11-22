"""输出匿名评审批次；同题三臂轮换位置。读者不应接触脚本、答案目录或评分表。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
# 分题交叉排列，映射只给主持人揭盲；风格仍可能让评审猜到来源。
ORDER = {
    'Q03': ('plain', 'public', 'candidate'),
    'Q09': ('candidate', 'plain', 'public'),
    'Q15': ('public', 'candidate', 'plain'),
    'Q23': ('plain', 'candidate', 'public'),
}


def samples(qid):
    return {
        label: (ROOT / 'answers' / arm / f'{qid}.md').read_text(encoding='utf-8-sig').strip()
        for label, arm in zip('ABC', ORDER[qid])
    }


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) != 2 or sys.argv[1] not in ORDER:
        sys.exit('用法：python blind.py ' + '|'.join(ORDER))
    print(json.dumps(samples(sys.argv[1]), ensure_ascii=False, indent=2))
