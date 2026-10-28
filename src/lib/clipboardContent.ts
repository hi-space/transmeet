import type { ClipboardContent } from '@/lib/clipboard'

/**
 * A table row becomes one list item when pasted into a message editor.
 * Keep the heading alongside each cell so the meaning survives without columns.
 */
function tableRowToListItem(
  row: HTMLTableRowElement,
  headings: string[],
  doc: Document
): HTMLLIElement {
  const item = doc.createElement('li')
  Array.from(row.cells).forEach((cell, index) => {
    if (index > 0) item.append(' · ')
    if (headings[index]) {
      const label = doc.createElement('strong')
      label.textContent = `${headings[index]}: `
      item.append(label)
    }
    item.append(...Array.from(cell.childNodes, (node) => node.cloneNode(true)))
  })
  return item
}

const ALLOWED_TAGS = new Set([
  'p',
  'strong',
  'b',
  'em',
  'i',
  'u',
  's',
  'blockquote',
  'pre',
  'code',
  'ul',
  'ol',
  'li',
  'a',
  'br',
  'hr',
])

const BLOCK_TAGS = new Set([
  'p',
  'blockquote',
  'pre',
  'ul',
  'ol',
  'li',
  'div',
  'section',
  'article',
  'header',
  'footer',
  'aside',
  'details',
  'summary',
  'figure',
  'figcaption',
  'hr',
])

function plainText(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.textContent ?? ''
    if (!text.trim()) {
      const adjacent = [node.previousSibling, node.nextSibling]
      if (
        adjacent.some(
          (sibling) =>
            !sibling ||
            (sibling.nodeType === Node.ELEMENT_NODE &&
              BLOCK_TAGS.has((sibling as Element).tagName.toLowerCase()))
        )
      )
        return ''
    }
    return text.replace(/\s+/g, ' ')
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return ''
  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  if (tag === 'br' || tag === 'hr') return '\n'
  if (tag === 'pre') return `${element.textContent ?? ''}\n\n`
  if (tag === 'code') return element.textContent ?? ''
  if (tag === 'ul' || tag === 'ol') {
    const start = Number(element.getAttribute('start') ?? 1)
    return (
      Array.from(element.children)
        .map((item, index) => {
          const marker = tag === 'ol' ? `${start + index}.` : '•'
          return `${marker} ${plainText(item).trim().replace(/\n/g, '\n  ')}`
        })
        .join('\n') + '\n\n'
    )
  }
  const text = Array.from(element.childNodes).reduce((result, child) => {
    const part = plainText(child)
    const startsBlock =
      child.nodeType === Node.ELEMENT_NODE &&
      BLOCK_TAGS.has((child as Element).tagName.toLowerCase())
    const separator = part && startsBlock && result && !result.endsWith('\n') ? '\n' : ''
    return result + separator + part
  }, '')
  if (tag === 'a') {
    const href = element.getAttribute('href')
    return href && href !== text ? `${text} (${href})` : text
  }
  return ['p', 'blockquote'].includes(tag) ? `${text}\n\n` : text
}

/** Normalize rendered content to the basic formatting accepted by message editors. */
export function createClipboardContent(
  rendered: HTMLElement,
  sourceUrl = rendered.baseURI
): ClipboardContent {
  const root = rendered.cloneNode(true) as HTMLElement
  const doc = root.ownerDocument
  root
    .querySelectorAll('script, style, iframe, object, embed, svg, math, template, noscript')
    .forEach((element) => element.remove())

  root.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach((heading) => {
    const paragraph = doc.createElement('p')
    const strong = doc.createElement('strong')
    strong.append(...Array.from(heading.childNodes))
    paragraph.append(strong)
    heading.replaceWith(paragraph)
  })

  root.querySelectorAll('table').forEach((table) => {
    const rows = Array.from(table.rows)
    const hasHeadings =
      rows.length > 1 && Array.from(rows[0].cells).every((cell) => cell.tagName === 'TH')
    const headings = hasHeadings
      ? Array.from(rows.shift()!.cells, (cell) => cell.textContent?.trim() ?? '')
      : []
    const list = doc.createElement('ul')
    rows.forEach((row) => list.append(tableRowToListItem(row, headings, doc)))
    const caption = table.querySelector(':scope > caption')
    if (caption) {
      const paragraph = doc.createElement('p')
      const emphasis = doc.createElement('em')
      emphasis.append(...Array.from(caption.childNodes))
      paragraph.append(emphasis)
      table.replaceWith(paragraph, list)
    } else {
      table.replaceWith(list)
    }
  })

  root.querySelectorAll('input').forEach((input) => {
    input.replaceWith(input.type === 'checkbox' ? (input.checked ? '☑ ' : '☐ ') : '')
  })
  root.querySelectorAll('img').forEach((image) => image.replaceWith(image.alt))
  root.querySelectorAll('del, mark').forEach((element) => {
    const replacement = doc.createElement(element.tagName === 'MARK' ? 'strong' : 's')
    replacement.append(...Array.from(element.childNodes))
    element.replaceWith(replacement)
  })

  // Layout containers need explicit paragraph boundaries after their CSS is removed.
  root
    .querySelectorAll(
      'div, section, article, header, footer, aside, details, summary, figure, figcaption'
    )
    .forEach((element) => {
      if (
        Array.from(element.children).some((child) => BLOCK_TAGS.has(child.tagName.toLowerCase()))
      ) {
        element.replaceWith(...Array.from(element.childNodes))
      } else {
        const paragraph = doc.createElement('p')
        paragraph.append(...Array.from(element.childNodes))
        element.replaceWith(paragraph)
      }
    })

  root.querySelectorAll('*').forEach((element) => {
    const tag = element.tagName.toLowerCase()
    if (!ALLOWED_TAGS.has(tag)) {
      element.replaceWith(...Array.from(element.childNodes))
      return
    }
    const href = element.getAttribute('href')
    const start = element.getAttribute('start')
    Array.from(element.attributes).forEach((attribute) => element.removeAttribute(attribute.name))
    if (tag === 'a' && href) {
      try {
        const url = new URL(href, sourceUrl)
        if (['https:', 'http:', 'mailto:', 'tel:'].includes(url.protocol)) {
          element.setAttribute('href', url.href)
        }
      } catch {
        // An invalid link remains readable text.
      }
    }
    if (tag === 'ol' && start && /^\d+$/.test(start)) element.setAttribute('start', start)
  })

  return { html: root.innerHTML, text: plainText(root).replace(/^\n+|\n+$/g, '') }
}

/** Prepare generated page semantics without interpreting its text as Markdown. */
export function createPageClipboardContent(html: string, sourceUrl: string): ClipboardContent {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const root = doc.body.querySelector('main') ?? doc.body
  root
    .querySelectorAll('nav, .toc, p.kicker, span.num, span.n')
    .forEach((element) => element.remove())

  root.querySelectorAll('svg').forEach((svg) => {
    const description = [
      svg.querySelector(':scope > title')?.textContent?.trim(),
      svg.querySelector(':scope > desc')?.textContent?.trim(),
    ]
      .filter(Boolean)
      .join(' — ')
    if (!description) {
      svg.remove()
      return
    }
    const paragraph = doc.createElement('p')
    const caption = doc.createElement('em')
    caption.textContent = `[그림] ${description}`
    paragraph.append(caption)
    svg.replaceWith(paragraph)
  })

  root.querySelectorAll('span.badge').forEach((badge) => {
    badge.textContent = `[${badge.textContent?.trim() ?? ''}]`
  })
  root.querySelectorAll('blockquote').forEach((quote) => {
    quote.querySelectorAll(':scope > .spk, :scope > .time').forEach((label) => {
      const strong = doc.createElement('strong')
      strong.append(...Array.from(label.childNodes))
      label.replaceWith(strong, ' ')
    })
    quote.querySelectorAll(':scope > .orig').forEach((original) => {
      const emphasis = doc.createElement('em')
      emphasis.append(...Array.from(original.childNodes))
      original.replaceWith(doc.createElement('br'), emphasis)
    })
  })

  root.querySelectorAll('.meta:not(dl)').forEach((meta) => {
    const list = doc.createElement('ul')
    Array.from(meta.childNodes).forEach((child) => {
      if (!child.textContent?.trim()) return
      const item = doc.createElement('li')
      item.append(child)
      list.append(item)
    })
    meta.replaceWith(list)
  })
  root.querySelectorAll('.stats li').forEach((item) => {
    Array.from(item.children)
      .slice(1)
      .forEach((child) => child.before(' '))
  })
  root.querySelectorAll('.topic-head').forEach((head) => {
    const heading = head.querySelector('h1, h2, h3, h4, h5, h6')
    if (!heading) return
    const rest = Array.from(head.childNodes)
      .filter((child) => child !== heading)
      .map((child) => child.textContent?.trim() ?? '')
      .filter(Boolean)
      .join(' ')
    if (rest) heading.append(` ${rest}`)
    head.replaceWith(heading)
  })
  root.querySelectorAll('dl').forEach((definitions) => {
    const list = doc.createElement('ul')
    const isMetadata = definitions.classList.contains('meta')
    let item: HTMLLIElement | undefined
    const entries = Array.from(definitions.querySelectorAll('dt, dd')).filter(
      (child) => child.closest('dl') === definitions
    )
    entries.forEach((child) => {
      if (child.tagName === 'DT' || !item) {
        item = doc.createElement('li')
        list.append(item)
      }
      if (child.tagName === 'DD' && isMetadata) {
        if (item.lastChild?.nodeName !== 'STRONG') item.append(' · ')
        item.append(...Array.from(child.childNodes))
        return
      }
      const part = doc.createElement(child.tagName === 'DT' ? 'strong' : 'p')
      part.append(...Array.from(child.childNodes))
      if (isMetadata) part.append(': ')
      item.append(part)
    })
    definitions.replaceWith(list)
  })

  return createClipboardContent(root, sourceUrl)
}
