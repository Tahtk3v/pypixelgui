# Third-party notices

The iLEDColor driver (`src/pypixelgui/drivers/`) is an independent implementation
of a reverse-engineered Bluetooth protocol. It is based on what these projects
documented and discovered; the gamma table and the test vectors in
`tests/test_iledcolor_protocol.py` come from set4npl/ledsign, and the raw-GIF
partition header values from zagi/iledcolor_small.

| Project | Used for | Licence |
|---|---|---|
| [set4npl/ledsign](https://github.com/set4npl/ledsign) | protocol spec, advertisement format, pixel formats, gamma table, test vectors | MIT, © 2026 Gaurav Pathak |
| [zagi/iledcolor_small](https://github.com/zagi/iledcolor_small) | connect/upload sequence, raw GIF header | MIT, © 2026 Michal Zagalski |
| [gwgorman/iledcolor-badge-mac](https://github.com/gwgorman/iledcolor-badge-mac) | monochrome "column16" pixel packing | MIT, © 2026 Greg Gorman |
| [akkaisinabin/iledcolor-rs](https://github.com/akkaisinabin/iledcolor-rs) | cross-checking the protocol description (no code used – it has no licence) | none |

The MIT licence of the three MIT projects:

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.
