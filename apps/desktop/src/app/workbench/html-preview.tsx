export function HtmlPreview({ text }: { text: string }) {
  return (
    <iframe
      className="h-full w-full border-0 bg-white"
      sandbox="allow-scripts"
      srcDoc={text}
      title="HTML preview"
    />
  )
}
