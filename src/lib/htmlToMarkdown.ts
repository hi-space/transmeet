// 미팅 페이지 HTML → Markdown 변환기.
//
// 에이전트가 만드는 페이지는 validate_page.py 가 허용하는 태그만 쓰므로 범용 라이브러리 대신
// 그 태그 집합만 다룬다. 페이지 고유 구조도 의미를 살려 옮긴다:
//   - <blockquote><span class="spk">화자 [시각]</span> 번역<span class="orig">원문</span></blockquote>
//     → "> **화자 [시각]** 번역" + "> _원문_"
//   - <figure><svg><title>…</title><desc>…</desc></svg><figcaption>…</figcaption></figure>
//     → 그림 자체는 옮길 수 없으니 제목·설명·캡션만 이탤릭 한 줄로
//   - <span class="badge">확인됨</span> → "[확인됨]"
//   - <mark> → **굵게** (표준 마크다운에 하이라이트가 없다)
//   - <nav class="toc">(목차), <p class="kicker">, <span class="num">(섹션 번호) → 생략. 복사한 텍스트에서는
//     헤딩 구조가 그 역할을 대신한다.
//   - <ul class="stats"> (주제 8 · 결정 6 …) → 한 줄
//   - <div class="topic-head"><span class="num"/><h3>…</h3><span class="time">…</span></div> → "### 제목 시각"

const BLOCK_TAGS = new Set([
  'address',
  'article',
  'aside',
  'blockquote',
  'details',
  'div',
  'dl',
  'dd',
  'dt',
  'figure',
  'figcaption',
  'footer',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'header',
  'hr',
  'li',
  'main',
  'nav',
  'ol',
  'p',
  'pre',
  'section',
  'summary',
  'table',
  'ul',
])

const SKIP_TAGS = new Set(['style', 'script', 'head', 'template', 'noscript'])

// 텍스트 노드의 공백(소스 HTML 의 들여쓰기 포함)은 전부 한 칸으로.
function collapse(text: string): string {
  return text.replace(/\s+/g, ' ')
}

// 이미 조합된 인라인 문자열: <br> 이 남긴 줄바꿈은 지키고 나머지 공백만 한 칸으로.
function collapseKeepBreaks(text: string): string {
  return text
    .replace(/[^\S\n]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .trim()
}

function escapeCell(text: string): string {
  return text.replace(/\|/g, '\\|').trim()
}

// 블록 사이를 빈 줄 하나로 정리하고, 줄 끝 공백을 지운다.
function tidy(md: string): string {
  return (
    md
      .split('\n')
      .map((l) => l.replace(/[ \t]+$/g, ''))
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim() + '\n'
  )
}

class Converter {
  // 인라인 콘텍스트에서 자식들을 이어 붙인다 (공백은 한 칸으로).
  inline(node: Node): string {
    let out = ''
    node.childNodes.forEach((child) => {
      out += this.node(child, true)
    })
    return out
  }

  // 블록 콘텍스트: 자식 블록들을 빈 줄로 구분한다. 블록 사이에 흩어진 인라인 텍스트는 문단으로 묶는다.
  blocks(node: Node): string {
    const parts: string[] = []
    let run = ''
    const flush = () => {
      const t = collapseKeepBreaks(run)
      if (t) parts.push(t)
      run = ''
    }
    node.childNodes.forEach((child) => {
      const tag =
        child.nodeType === Node.ELEMENT_NODE ? (child as Element).tagName.toLowerCase() : ''
      if (tag && BLOCK_TAGS.has(tag)) {
        flush()
        const block = this.node(child, false)
        if (block.trim()) parts.push(block.replace(/^\n+|\n+$/g, ''))
      } else {
        run += this.node(child, true)
      }
    })
    flush()
    return parts.join('\n\n')
  }

  node(node: Node, inlineCtx: boolean): string {
    if (node.nodeType === Node.TEXT_NODE) {
      return collapse(node.textContent ?? '')
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return ''
    const el = node as Element
    const tag = el.tagName.toLowerCase()
    if (SKIP_TAGS.has(tag)) return ''
    if (tag === 'nav' || el.classList.contains('toc')) return ''
    if (tag === 'p' && el.classList.contains('kicker')) return ''

    switch (tag) {
      case 'svg':
        return this.svg(el)
      case 'br':
        return '\n'
      case 'hr':
        return '\n\n---\n\n'
      case 'h1':
      case 'h2':
      case 'h3':
      case 'h4':
      case 'h5':
      case 'h6': {
        const level = Number(tag[1])
        return `\n\n${'#'.repeat(level)} ${this.inline(el).trim()}\n\n`
      }
      case 'p':
        return `\n\n${collapseKeepBreaks(this.inline(el))}\n\n`
      case 'blockquote':
        return `\n\n${this.blockquote(el)}\n\n`
      case 'ul':
      case 'ol':
        if (el.classList.contains('stats')) return `\n\n${this.stats(el)}\n\n`
        return `\n\n${this.list(el, tag === 'ol')}\n\n`
      case 'li':
        // list() 가 처리한다. 부모 없이 홀로 오면 불릿으로.
        return `\n\n- ${this.blocks(el)}\n\n`
      case 'dl':
        return `\n\n${this.dl(el)}\n\n`
      case 'table':
        return `\n\n${this.table(el)}\n\n`
      case 'pre':
        return `\n\n\`\`\`\n${(el.textContent ?? '').replace(/\n$/, '')}\n\`\`\`\n\n`
      case 'figure':
        return `\n\n${this.figure(el)}\n\n`
      case 'figcaption':
        return `_${this.inline(el).trim()}_`
      case 'details':
      case 'summary':
      case 'header':
      case 'footer':
      case 'section':
      case 'article':
      case 'main':
      case 'nav':
      case 'aside':
      case 'div':
        if (el.classList.contains('topic-head')) return `\n\n${this.topicHead(el)}\n\n`
        if (!inlineCtx && el.classList.contains('meta')) return `\n\n${this.meta(el)}\n\n`
        if (!inlineCtx && el.classList.contains('ref')) return `\n\n${this.refCard(el)}\n\n`
        return inlineCtx ? this.inline(el) : `\n\n${this.blocks(el)}\n\n`
      case 'b':
      case 'strong':
      case 'mark':
        return this.wrap(this.inline(el), '**')
      case 'i':
      case 'em':
        return this.wrap(this.inline(el), '_')
      case 'code': {
        const text = (el.textContent ?? '').trim()
        return text ? `\`${text}\`` : ''
      }
      case 'a': {
        const text = this.inline(el).trim()
        const href = el.getAttribute('href') ?? ''
        // 페이지 내부 앵커(#topic-2)는 복사한 텍스트에서 의미가 없으니 글자만 남긴다.
        if (!href || href.startsWith('#')) return text
        return text ? `[${text}](${href})` : href
      }
      case 'span': {
        if (el.classList.contains('num') || el.classList.contains('n')) return ''
        const text = this.inline(el)
        if (el.classList.contains('badge')) return text.trim() ? `[${text.trim()}]` : ''
        return text
      }
      default:
        return inlineCtx ? this.inline(el) : this.blocks(el)
    }
  }

  // "**텍스트**" 처럼 감쌀 때 안쪽 공백은 밖으로 뺀다 (안쪽에 공백이 있으면 마크다운이 인식하지 못한다).
  wrap(text: string, mark: string): string {
    const lead = text.match(/^\s*/)?.[0] ?? ''
    const trail = text.match(/\s*$/)?.[0] ?? ''
    const core = text.trim()
    if (!core) return text
    return `${lead}${mark}${core}${mark}${trail}`
  }

  // 헤더 메타(일시·소요·참석자…): <span> 하나가 항목 하나 → 불릿 하나.
  meta(el: Element): string {
    const items: string[] = []
    el.childNodes.forEach((child) => {
      const text = collapseKeepBreaks(this.node(child, true))
      if (text) items.push(`- ${text}`)
    })
    return items.join('\n')
  }

  // 통계 칩: <li><b>8</b><span>주제</span></li> … → "**8** 주제 · **6** 결정"
  stats(el: Element): string {
    const items = Array.from(el.children)
      .map((li) =>
        Array.from(li.childNodes)
          .map((c) => this.node(c, true).trim())
          .filter(Boolean)
          .join(' ')
      )
      .filter(Boolean)
    return items.join(' · ')
  }

  // 주제 머리: 번호는 버리고 "### 제목 시각" 한 줄로.
  topicHead(el: Element): string {
    const h = el.querySelector('h1, h2, h3, h4, h5, h6')
    const level = h ? Number(h.tagName[1]) : 3
    const title = h ? collapseKeepBreaks(this.inline(h)) : ''
    const rest = Array.from(el.childNodes)
      .filter((c) => c !== h)
      .map((c) => this.node(c, true).trim())
      .filter(Boolean)
      .join(' ')
    return `${'#'.repeat(level)} ${[title, rest].filter(Boolean).join(' ')}`
  }

  // 관련 자료 카드: 첫 줄(링크·배지)이 불릿, 나머지 .why 줄들은 하위 불릿.
  refCard(el: Element): string {
    const lines = Array.from(el.children)
      .map((child) => collapseKeepBreaks(this.inline(child)).replace(/\n/g, ' '))
      .filter(Boolean)
    if (!lines.length) return ''
    const [head, ...rest] = lines
    return [`- ${head}`, ...rest.map((l) => `  - ${l}`)].join('\n')
  }

  svg(el: Element): string {
    const title = el.querySelector(':scope > title')?.textContent?.trim()
    const desc = el.querySelector(':scope > desc')?.textContent?.trim()
    const parts = [title, desc].filter(Boolean)
    return parts.length ? `_[그림] ${parts.join(' — ')}_` : ''
  }

  // 그림: svg 의 제목·설명 한 줄 + 캡션 한 줄. 캡션이 제목을 그대로 되풀이하면 한 번만 적는다.
  figure(el: Element): string {
    const lines: string[] = []
    el.childNodes.forEach((child) => {
      const text = this.node(child, false).trim()
      if (!text) return
      const bare = text.replace(/^_\[그림\] |^_|_$/g, '')
      if (lines.some((l) => l.replace(/^_\[그림\] |^_|_$/g, '').includes(bare))) return
      lines.push(text)
    })
    return lines.join('  \n')
  }

  blockquote(el: Element): string {
    // 발화 인용: 앞머리의 화자/시각 라벨(.spk, .time)은 굵게, 원문(.orig)은 다음 줄에 이탤릭으로.
    const children = Array.from(el.childNodes)
    const isLabel = (n: Node) =>
      n.nodeType === Node.ELEMENT_NODE &&
      ((n as Element).classList.contains('spk') || (n as Element).classList.contains('time'))
    const isOrig = (n: Node) =>
      n.nodeType === Node.ELEMENT_NODE && (n as Element).classList.contains('orig')
    const labels: string[] = []
    let i = 0
    while (i < children.length) {
      const c = children[i]
      if (isLabel(c)) labels.push(collapse(c.textContent ?? '').trim())
      else if (c.nodeType === Node.TEXT_NODE && !(c.textContent ?? '').trim()) {
        // 라벨 사이 공백
      } else break
      i += 1
    }
    const origs = children.filter(isOrig)
    if (labels.length || origs.length) {
      let body = ''
      children.slice(i).forEach((c) => {
        if (!isOrig(c)) body += this.node(c, true)
      })
      const lines: string[] = []
      const label = labels.filter(Boolean).join(' ')
      const main = [label ? `**${label}**` : '', collapseKeepBreaks(body)].filter(Boolean).join(' ')
      if (main) lines.push(...main.split('\n'))
      origs.forEach((o) => {
        const t = collapse(o.textContent ?? '').trim()
        if (t) lines.push(`_${t}_`)
      })
      return lines.map((l) => `> ${l}`).join('\n')
    }
    return this.blocks(el)
      .split('\n')
      .map((l) => (l ? `> ${l}` : '>'))
      .join('\n')
  }

  list(el: Element, ordered: boolean, depth = 0): string {
    const indent = '  '.repeat(depth)
    const items: string[] = []
    let n = 1
    Array.from(el.children).forEach((li) => {
      if (li.tagName.toLowerCase() !== 'li') return
      // 중첩 목록은 분리해서 아래에 들여쓰기로 붙인다.
      const nested: Element[] = []
      let text = ''
      li.childNodes.forEach((child) => {
        const tag =
          child.nodeType === Node.ELEMENT_NODE ? (child as Element).tagName.toLowerCase() : ''
        if (tag === 'ul' || tag === 'ol') nested.push(child as Element)
        else text += this.node(child, true)
      })
      const marker = ordered ? `${n}.` : '-'
      n += 1
      const head = collapseKeepBreaks(text).replace(/\n/g, `\n${indent}  `)
      let item = `${indent}${marker} ${head}`
      nested.forEach((sub) => {
        item += `\n${this.list(sub, sub.tagName.toLowerCase() === 'ol', depth + 1)}`
      })
      items.push(item)
    })
    return items.join('\n')
  }

  // 질문과 답(<dl>): 질문은 굵은 불릿, 답은 그 아래 들여쓴 줄. (정의 목록은 GFM 에 없다)
  dl(el: Element): string {
    const lines: string[] = []
    Array.from(el.children).forEach((child) => {
      const tag = child.tagName.toLowerCase()
      const text = collapseKeepBreaks(this.inline(child)).replace(/\n/g, ' ')
      if (!text) return
      if (tag === 'dt') lines.push(`- **${text}**`)
      else if (tag === 'dd') lines.push(`  ${text}`)
    })
    return lines.join('\n')
  }

  table(el: Element): string {
    const rows: string[][] = []
    el.querySelectorAll('tr').forEach((tr) => {
      const cells: string[] = []
      tr.querySelectorAll('th, td').forEach((cell) => {
        // 셀 안의 <br> 은 표를 깨지 않도록 공백으로.
        cells.push(escapeCell(collapseKeepBreaks(this.inline(cell)).replace(/\n/g, ' ')))
      })
      if (cells.length) rows.push(cells)
    })
    if (!rows.length) return ''
    const width = Math.max(...rows.map((r) => r.length))
    const pad = (r: string[]) => [...r, ...Array(width - r.length).fill('')]
    const hasHeader = !!el.querySelector('thead') || !!el.querySelector('th')
    const header = hasHeader ? pad(rows[0]) : Array(width).fill('')
    const body = hasHeader ? rows.slice(1) : rows
    const line = (r: string[]) => `| ${r.join(' | ')} |`
    const out = [
      line(header),
      `| ${Array(width).fill('---').join(' | ')} |`,
      ...body.map((r) => line(pad(r))),
    ]
    const caption = el.querySelector(':scope > caption')
    if (caption) out.unshift(`_${this.inline(caption).trim()}_`, '')
    return out.join('\n')
  }
}

/** 미팅 페이지 HTML 문자열을 Markdown 으로 바꾼다. 브라우저(DOMParser) 전용. */
export function htmlToMarkdown(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const root = doc.body.querySelector('main') ?? doc.body
  return tidy(new Converter().blocks(root))
}
