# Third-party notices

The MIT license in this repository covers this project's original source. It does not replace upstream dependency licenses.

## Bouncy Castle

The Java transport uses these Maven artifacts:

- `org.bouncycastle:bcprov-jdk18on:1.86`
- `org.bouncycastle:bcutil-jdk18on:1.86`
- `org.bouncycastle:bctls-jdk18on:1.86.2`

Dependencies are downloaded during the build, not checked into Git. Their embedded `META-INF/LICENSE.md` contains the following notice (verified from the dependency archive):

> MIT License (https://opensource.org/licenses/MIT)
>
> Copyright (c) 2000-2026 The Legion of the Bouncy Castle Inc. (https://www.bouncycastle.org).
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute,
> sub license, and/or sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions: The above copyright notice and this
> permission notice shall be included in all copies or substantial portions of the Software.
>
> **THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
> NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.**

Distributors must preserve the license embedded in each jar and include this notice with redistributed source/build artifacts.

## External runtime tools

Python, Java, Docker and the selected Jellyfin FFmpeg image are separately supplied runtime tools. This source repository does not redistribute those installations or a media library. Consult the chosen image's upstream licenses if you redistribute an image. Example product names do not imply affiliation.
