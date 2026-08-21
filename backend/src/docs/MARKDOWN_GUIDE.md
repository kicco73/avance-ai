# Markdown Guide

A minimal reference for the Markdown syntax this editor's Preview renders.

## Headings

```
# Heading 1
## Heading 2
### Heading 3
```

## Emphasis

```
*italic* or _italic_
**bold** or __bold__
~~strikethrough~~
```

## Lists

```
- item
- item
  - nested item

1. first
2. second
```

## Links and images

```
[link text](https://example.com)
![alt text](https://example.com/image.png)
```

## Code

Inline code: `` `like this` ``

Fenced block, optionally with a language for highlighting:

````
```js
const answer = 42
```
````

## Blockquotes

```
> quoted text
```

## Tables

```
| Column A | Column B |
| -------- | -------- |
| a1       | b1       |
```

## Horizontal rule

```
---
```

## A few notes

- A single line break already starts a new line — no trailing blank line or two trailing spaces needed.
- Raw HTML tags aren't rendered; stick to plain Markdown.
- A bare URL (e.g. `https://example.com`) is turned into a clickable link automatically.
