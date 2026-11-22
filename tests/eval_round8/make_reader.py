"""生成离线盲读页：python -B tests/eval_round8/make_reader.py。只把 reader.html 发给读者。"""
import json
from pathlib import Path

from blind import samples


# 主持人用包号和从 1 起算的呈现顺序还原题号及匿名标签，再用 blind.py 揭盲。
# 每题覆盖三份首答、三个不同位置；每包三臂均出现（2/1/1），相邻报告不来自同一臂。
PACKS = {
    '01': (('Q03', 'A'), ('Q09', 'C'), ('Q15', 'B'), ('Q23', 'C')),
    '02': (('Q09', 'B'), ('Q03', 'C'), ('Q23', 'A'), ('Q15', 'A')),
    '03': (('Q23', 'B'), ('Q15', 'C'), ('Q03', 'B'), ('Q09', 'A')),
}


def as_script_data(value):
    """HTML 解析先于 JSON 解析；原答中的结束标签不得逃出数据区。"""
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return encoded.translate({ord(c): f'\\u{ord(c):04x}' for c in '<>&\u2028\u2029'})


PAGE = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>四份报告｜离线盲读</title>
<style>
:root { color-scheme: light dark; --paper: #f5f7fa; --sheet: #fff; --ink: #18263b; --muted: #465970; --edge: #708298; --action: #214984; --on-action: #fff; --focus: #976400 }
@media (prefers-color-scheme: dark) { :root { --paper: #181d24; --sheet: #232a33; --ink: #f0f3f7; --muted: #c0cbd8; --edge: #8e9cab; --action: #aecaff; --on-action: #16273d; --focus: #edc47d } }
* { box-sizing: border-box }
[hidden] { display: none !important }
body { margin: 0; color: var(--ink); background: var(--paper); font: 1rem/1.65 'Microsoft YaHei', 'PingFang SC', sans-serif }
main { max-width: 46rem; margin: auto; padding: clamp(1rem, 4vw, 3rem) 1rem 4rem }
header { margin-bottom: 2rem; border-bottom: 2px solid var(--action); padding-bottom: .75rem }
h1, h2 { margin: 0; line-height: 1.4 }
h1 { font-size: 1.15rem } h2 { font-size: clamp(1.5rem, 4vw, 2rem) }
p { margin: .75rem 0 1rem } .note, #status { color: var(--muted) }
#status { margin: .25rem 0 0; font-variant-numeric: tabular-nums }
#text { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--sheet); padding: clamp(.875rem, 3vw, 1.75rem); border-inline-start: .25rem solid var(--action); margin: 1rem 0; line-height: 1.85 }
label { display: block; margin: 1rem 0 .35rem; font-weight: 650 }
select, textarea, button { font: inherit; color: var(--ink); background-color: var(--sheet); border: 1px solid var(--edge); border-radius: .25rem }
select, button { min-height: 2.75rem; padding: .45rem .85rem } select { width: min(100%, 16rem) }
textarea { display: block; width: 100%; min-height: 5rem; padding: .5rem .75rem; resize: vertical }
button { cursor: pointer; touch-action: manipulation } .primary { color: var(--on-action); background: var(--action); border-color: var(--action) }
button:hover { text-decoration: underline } button:active { transform: translateY(1px) }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px }
.actions { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1rem }
</style>
</head>
<body><main>
<header><h1>四份报告｜离线盲读</h1><p id="status" aria-live="polite">个人阅读与复述</p></header>
<noscript><p>请用启用了 JavaScript 的本地浏览器打开此文件；不需要联网。</p></noscript>
<section id="setup"><h2>领取你的阅读包</h2>
<p>请主持人把 01、02、03 分别交给三位不同的读者。每人只做自己的一个包，不交换、不讨论；如果已看过其中的材料，请先告诉主持人。</p>
<p>每份报告只读一次。准备好才开始；读完即隐藏原文，然后用自己的话复述。不查资料，不复制、不截图。请不要写姓名或联系方式。</p>
<p class="note">网页不联网、不自动保存，也不能在刷新或关闭后续读。每完成一份都可以下载当前答卷；中断请将最近导出的文件交给主持人，说明已读到哪里，不要重读补测。</p>
<form id="choose" autocomplete="off"><label for="pack">主持人分配的包号</label>
<select id="pack" required><option value="">请选择包号</option>@@OPTIONS@@</select>
<div class="actions"><button class="primary">开始阅读第一份</button></div></form></section>
<section id="reading" hidden><h2 tabindex="-1">阅读报告</h2><p class="note">从原文展示到按下读完按钮计时，切换窗口不会主动暂停。请按平时的速度阅读。</p>
<article id="text" aria-label="本份报告原文"></article><button type="button" id="finish" class="primary">读完，隐藏原文</button></section>
<section id="recall" hidden><h2 tabindex="-1">凭记忆复述</h2><p>原文已隐藏。请分别用自己的话填写；记不起来就写“记不清”，不必猜测。</p>
<form id="response" autocomplete="off">
<label for="conclusion">结论是什么？</label><textarea id="conclusion" required spellcheck="false"></textarea>
<label for="limitation">结论受什么条件限制？</label><textarea id="limitation" required spellcheck="false"></textarea>
<label for="next_step">下一步具体做什么？</label><textarea id="next_step" required spellcheck="false"></textarea>
<div class="actions"><button class="primary">确认本份复述</button></div></form></section>
<section id="ready" hidden><h2 tabindex="-1" id="ready-title"></h2><p id="ready-note"></p>
<div class="actions"><button type="button" id="next" class="primary">开始阅读下一份</button><button type="button" id="download">导出匿名答卷 JSON</button></div>
<p class="note">只有点击导出才会生成文件，不会上传。请确认文件已保存；同一包只交最后一次导出的文件。页面不能从导出文件恢复。</p></section>
</main>
<script type="application/json" id="reports">@@REPORTS@@</script>
<script>
'use strict';
const el = id => document.getElementById(id);
const reports = JSON.parse(el('reports').textContent);
const fields = ['conclusion', 'limitation', 'next_step'];
const answers = [];
let pack, started, seconds;

function show(id) {
  for (const section of document.querySelectorAll('main > section')) section.hidden = section.id !== id;
  const title = el(id).querySelector('h2');
  title.focus({preventScroll: true});
  window.scrollTo(0, 0);
}
function readOne() {
  const position = answers.length + 1;
  el('status').textContent = `包 ${pack} ｜ 第 ${position} 份，共 ${reports[pack].length} 份`;
  el('text').textContent = reports[pack][position - 1];
  show('reading');
  started = performance.now();
}
el('choose').addEventListener('submit', event => {
  event.preventDefault();
  pack = el('pack').value;
  readOne();
});
el('next').addEventListener('click', readOne);
el('finish').addEventListener('click', () => {
  seconds = Math.round(performance.now() - started) / 1000;
  el('text').textContent = '';
  show('recall');
});
el('response').addEventListener('submit', event => {
  event.preventDefault();
  const answer = {position: answers.length + 1, reading_seconds: seconds};
  for (const name of fields) {
    const input = el(name);
    if (!input.value.trim()) {
      input.setCustomValidity('请填写你的复述；记不起来可以写“记不清”。');
      input.reportValidity();
      input.setCustomValidity('');
      return;
    }
    answer[name] = input.value;
  }
  answers.push(answer);
  el('response').reset();
  const done = answers.length === reports[pack].length;
  el('status').textContent = `包 ${pack} ｜ 已完成 ${answers.length} / ${reports[pack].length} 份`;
  el('ready-title').textContent = done ? '四份都已完成' : '这份复述已记录';
  el('ready-note').textContent = done ? '请导出答卷，将文件交给主持人。不要再做其他包。' : '可以休息一下，下一份还没开始计时。现在也可以先导出已完成的部分。';
  el('next').hidden = done;
  el('download').classList.toggle('primary', done);
  show('ready');
});
el('download').addEventListener('click', () => {
  const result = {protocol: 'round8-reader-v1', pack, responses: answers};
  const blob = new Blob([JSON.stringify(result, null, 2)], {type: 'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `round8-pack-${pack}-${answers.length}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});
</script></body></html>
'''


def build():
    by_question = {qid: samples(qid) for qid, _ in PACKS['01']}
    reports = {pack: [by_question[qid][label] for qid, label in sequence]
               for pack, sequence in PACKS.items()}
    if not all(text.strip() for group in reports.values() for text in group):
        raise ValueError('原答不能为空，未生成盲读页。')
    options = ''.join(f'<option value="{pack}">{pack}</option>' for pack in PACKS)
    return PAGE.replace('@@OPTIONS@@', options).replace('@@REPORTS@@', as_script_data(reports))


if __name__ == '__main__':
    Path(__file__).with_name('reader.html').write_text(build(), encoding='utf-8')
