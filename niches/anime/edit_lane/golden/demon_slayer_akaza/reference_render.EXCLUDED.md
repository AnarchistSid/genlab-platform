# reference_render.mp4 is deliberately NOT in git

It is our own render, but it carries Demon Slayer footage that Aniplex blocks
worldwide under Content ID (CLM-001). Licensed footage does not go in the repo.

    sha256  f8dbfb2f4420deeb8021fb271c420d73bdee09114c76d6874112ec6a13e75f77
    bytes   60144218

Nothing is lost. `sources.json` pins every input by sha256 + URL + uploader, and
`approved_plan.json` is the full cut, so the render re-derives. The hash above is
here so a recovered copy can be proved identical to the approved one.
