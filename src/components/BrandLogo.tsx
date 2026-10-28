interface Props {
  size?: 'header' | 'auth'
  className?: string
}

/** Shared SVG artwork keeps the logo independent of the reading-font setting. */
export default function BrandLogo({ size = 'header', className = '' }: Props) {
  const isAuth = size === 'auth'

  return (
    <span
      role="img"
      aria-label="TransMeet"
      className={`inline-flex min-w-0 items-center ${isAuth ? 'gap-3' : 'gap-2'} ${className}`}
    >
      <svg
        viewBox="0 0 64 64"
        aria-hidden="true"
        focusable="false"
        className={`shrink-0 ${isAuth ? 'h-12 w-12' : 'h-8 w-8'}`}
      >
        <use
          href="/brand/transmeet.svg#transmeet-left"
          className="fill-[#176b62] dark:fill-[#c4f0e2]"
        />
        <use href="/brand/transmeet.svg#transmeet-right" className="fill-[#51bca2]" />
      </svg>
      <svg
        viewBox="0 0 224 34"
        aria-hidden="true"
        focusable="false"
        className={`min-w-0 fill-[#153e39] dark:fill-[#e6f4ee] ${
          isAuth ? 'h-[26px] w-[171px]' : 'h-[18px] w-[119px] max-[374px]:hidden'
        }`}
      >
        <use href="/brand/transmeet.svg#transmeet-wordmark" />
      </svg>
    </span>
  )
}
