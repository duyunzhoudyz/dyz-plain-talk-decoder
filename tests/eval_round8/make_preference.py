"""生成偏好选票：python -B tests/eval_round8/make_preference.py。只分发 preference.html。"""
import json
from pathlib import Path

from blind import samples
from make_reader import as_script_data


ROOT = Path(__file__).resolve().parent
QUESTIONS = ('Q03', 'Q09', 'Q15', 'Q23')
# 前三包构成拉丁方；后三包反向排列。标签只决定展示顺序，不重命名原答。
LABEL_ORDERS = ('ABC', 'BCA', 'CAB', 'CBA', 'BAC', 'ACB')


def build():
    cases = {row['id']: row for row in json.loads((ROOT / 'cases.json').read_text(encoding='utf-8'))}
    questions = {}
    for qid in QUESTIONS:
        answers = samples(qid)
        if set(answers) != set('ABC') or not all(text.strip() for text in answers.values()):
            raise ValueError(f'{qid} 的三份匿名原答不完整。')
        questions[qid] = {'prompt': cases[qid], 'answers': answers}
    packs = {}
    for i, order in enumerate(LABEL_ORDERS):
        sequence = []
        for position in range(len(QUESTIONS)):
            q = (position + i) % len(QUESTIONS)
            offset = q % 3
            sequence.append({'qid': QUESTIONS[q], 'labels': order[offset:] + order[:offset]})
        packs[f'P{i + 1:02}'] = sequence
    return PAGE.replace('@@DATA@@', as_script_data({'questions': questions, 'packs': packs}))


PAGE = '''<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'">
<title>四题匿名偏好选票</title>
<style>
:root {color-scheme:light dark;--paper:#f5f7fa;--sheet:#fff;--ink:#18263b;--muted:#465970;--edge:#708298;--action:#214984;--on-action:#fff;--focus:#976400}
@media(prefers-color-scheme:dark){:root{--paper:#181d24;--sheet:#232a33;--ink:#f0f3f7;--muted:#c0cbd8;--edge:#8e9cab;--action:#aecaff;--on-action:#16273d;--focus:#edc47d}}
*{box-sizing:border-box} [hidden]{display:none!important} body{margin:0;background:var(--paper);color:var(--ink);font:1rem/1.7 'Microsoft YaHei','PingFang SC',sans-serif}
main{margin:auto;max-width:48rem;padding:clamp(1rem,4vw,3rem) 1rem 4rem} header{border-bottom:2px solid var(--action);margin-bottom:1.5rem} h1{font-size:1.2rem} h2{font-size:1.55rem} h3{font-size:1.13rem} p{margin:.65rem 0 1rem} .note,#status{color:var(--muted)}
#material,article{padding:clamp(.9rem,3vw,1.6rem);background:var(--sheet);margin:1rem 0} article{border-inline-start:.25rem solid var(--edge)} article h3,#material h3{margin:0 0 .5rem} article p,#material p{white-space:pre-wrap;overflow-wrap:anywhere} #material ul{padding-inline-start:1.5rem}
label{display:block;font-weight:650;margin:1rem 0 .25rem} select,button,textarea,option{font:inherit;color:var(--ink);background-color:var(--sheet)} select,button,textarea{border:1px solid var(--edge);border-radius:.25rem;max-width:100%} select,button{min-height:2.75rem;padding:.45rem .8rem} textarea{width:100%;min-height:5rem;padding:.5rem;resize:vertical} button{cursor:pointer;touch-action:manipulation} .primary{background:var(--action);color:var(--on-action);border-color:var(--action)} button:hover{text-decoration:underline} button:active{transform:translateY(1px)} :focus-visible{outline:3px solid var(--focus);outline-offset:3px} .actions{display:flex;flex-wrap:wrap;gap:.75rem;margin-top:1rem}
</style></head>
<body><main><header><h1>四题匿名偏好选票</h1><p id="status" aria-live="polite">个人选择，不需要猜答案来自哪里。</p></header>
<noscript>请用启用了 JavaScript 的浏览器打开本文件；不需要联网。</noscript>
<section id="setup"><h2>如果自己要用，你会选哪份？</h2>
<p>每题先看题面和三份完整原答，再选最愿意用的一份，也可以并列或都不选。请独立完成，不与其他读者讨论。</p>
<p><strong>参加过本轮单答复述、读过这些答案或看过评分的人，不做这张选票。</strong>请告诉主持人另换一位读者。主持人把 P01 至 P06 顺序分给不同的新读者；一人一包，满六人再循环。不要自行换包或重做。</p>
<p class="note">阅读和选择分别计时；两题之间可休息。页面不联网、不自动保存，不收集姓名或联系方式。刷新或关闭会丢失未导出的内容，每做完一题都可以导出。</p>
<form id="setup-form" autocomplete="off"><label for="pack">主持人分配的包号</label><select id="pack" required><option value="">请选择包号</option></select>
<div class="actions"><button class="primary">开始第一题</button></div></form></section>
<section id="task" hidden><h2 id="task-title" tabindex="-1"></h2><p class="note">先读题面和三份答案。阅读段计的是这整段时间，不是任意一份答案的单独阅读时间。切换窗口不暂停。</p>
<div id="material"><h3>原题</h3><div id="prompt"></div></div><div id="reports"></div>
<button id="finish-read" type="button" class="primary">三份已读完，开始选择</button>
<form id="vote-form" hidden autocomplete="off"><h2 id="vote-title" tabindex="-1">你会用哪份答案？</h2><p class="note">可以向上回看。回看、选择和写理由的时间都计入选择段。</p>
<label for="favorite">最愿意使用</label><select id="favorite" required></select>
<label for="why">为什么这样选？</label><p class="note" id="why-hint">可以写哪份漏了重要条件、哪条建议能照做，或有没有无依据的指控；也可以写自己的理由。请不要写个人资料。不便说明可写“不便说明”；跳过本题不必写。</p>
<textarea id="why" aria-describedby="why-hint" spellcheck="false"></textarea><div class="actions"><button class="primary">确认本题选择</button></div></form></section>
<section id="ready" hidden><h2 id="ready-title" tabindex="-1"></h2><p id="ready-note"></p><div class="actions"><button class="primary" type="button" id="next">开始下一题</button><button type="button" id="download">导出匿名选票 JSON</button></div>
<p class="note">只有点击导出才会写入下载文件，不会上传。请手动把最后一次导出的 JSON 交给主持人；中途退出就交现有的部分选票，不重做。确认已保存后再关闭。</p></section>
</main><script type="application/json" id="data">@@DATA@@</script>
<script>
'use strict';
const el = id => document.getElementById(id);
const {questions, packs} = JSON.parse(el('data').textContent);
const votes = [];
let pack, ballotId, start, reviewSeconds;
function add(parent, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  parent.append(node);
  return node;
}
function screen(id, heading) {
  for (const section of document.querySelectorAll('main > section')) section.hidden = section.id !== id;
  el(heading).focus({preventScroll:true});
  window.scrollTo(0, 0);
}
function secondsSinceStart() { return Math.round(performance.now() - start) / 1000; }
function beginQuestion() {
  const {qid, labels} = packs[pack][votes.length];
  const {prompt, answers} = questions[qid];
  el('status').textContent = `${pack} ｜ 第 ${votes.length + 1} / ${packs[pack].length} 题`;
  el('task-title').textContent = `${qid}　比较三份回答`;
  const material = el('prompt');
  material.replaceChildren();
  add(material, 'p', prompt.request);
  add(material, 'p', prompt.text);
  if (prompt.profile) add(material, 'p', prompt.profile);
  if (prompt.evidence) {
    add(material, 'h4', '题目附带的检索记录');
    const list = add(material, 'ul', '');
    for (const item of prompt.evidence) add(list, 'li', `${item.id}　${item.source}（${item.access}）：${item.content}`);
  }
  el('reports').replaceChildren();
  for (const label of labels) {
    const report = add(el('reports'), 'article', '');
    add(report, 'h3', `答案 ${label}`);
    add(report, 'p', answers[label]);
  }
  const [a,b,c] = labels;
  const select = el('favorite');
  select.replaceChildren(new Option('请选择', ''));
  for (const value of [a,b,c,a+b,a+c,b+c,labels]) {
    select.add(new Option(value.length === 1 ? `答案 ${value}` : `并列：${[...value].join('、')}`, value));
  }
  select.add(new Option('都不选：三份都不愿用', 'none'));
  select.add(new Option('跳过：本题不投票', 'skip'));
  el('vote-form').reset();
  el('vote-form').hidden = true;
  el('finish-read').hidden = false;
  screen('task', 'task-title');
  start = performance.now();
}
for (const p of Object.keys(packs)) el('pack').add(new Option(p, p));
el('setup-form').addEventListener('submit', event => {
  event.preventDefault();
  pack = el('pack').value;
  // 同一读者的阶段性导出共用随机票号；同包号可分配给不同读者。
  ballotId = Array.from(crypto.getRandomValues(new Uint32Array(3)), n => n.toString(16).padStart(8,'0')).join('');
  beginQuestion();
});
el('finish-read').addEventListener('click', () => {
  reviewSeconds = secondsSinceStart();
  el('finish-read').hidden = true;
  el('vote-form').hidden = false;
  el('vote-title').focus();
  start = performance.now();
});
el('vote-form').addEventListener('submit', event => {
  event.preventDefault();
  const value = el('favorite').value, why = el('why').value.trim();
  if (value !== 'skip' && !why) {
    el('why').setCustomValidity('请写下理由；不便说明可以直接写“不便说明”。');
    el('why').reportValidity();
    el('why').setCustomValidity('');
    return;
  }
  const {qid, labels} = packs[pack][votes.length];
  const choice = value === 'none' || value === 'skip' ? value : value.length === 1 ? 'single' : 'tie';
  votes.push({qid, position:votes.length + 1, display_order:[...labels], choice,
    selected_labels:choice === 'none' || choice === 'skip' ? [] : [...value].sort(), reason:why,
    three_answer_review_seconds:reviewSeconds, selection_seconds:secondsSinceStart()});
  const done = votes.length === packs[pack].length;
  el('ready-title').textContent = done ? '四题已结束' : '本题选择已记录';
  el('ready-note').textContent = done ? '请导出选票并交给主持人，不要再做其他包。' : '可先导出已完成的部分，休息后再开始下一题。';
  el('status').textContent = `${pack} ｜ 已记录 ${votes.length} / ${packs[pack].length} 题`;
  el('next').hidden = done;
  el('download').classList.toggle('primary', done);
  screen('ready', 'ready-title');
});
el('next').addEventListener('click', beginQuestion);
el('download').addEventListener('click', () => {
  const result = {protocol:'round8-preference-v1', ballot_id:ballotId, pack,
    completed:votes.length === packs[pack].length, responses:votes};
  const blob = new Blob([JSON.stringify(result, null, 2)], {type:'application/json'});
  const link = document.createElement('a');
  const url = URL.createObjectURL(blob);
  link.href = url;
  link.download = `round8-preference-${pack}-${ballotId}-${votes.length}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
</script></body></html>
'''


if __name__ == '__main__':
    (ROOT / 'preference.html').write_text(build(), encoding='utf-8')
