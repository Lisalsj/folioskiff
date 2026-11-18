# PDF security fixtures

The reader security suite creates every PDF fixture at test time. No downloaded,
copyrighted, private, or opaque binary PDF is stored in this repository.

`tests.reader.pdf_fixtures` can generate normal text/image documents as well as
minimal hostile structures: an encrypted file, truncated object graph,
self-referential page tree, oversized MediaBox, excessive page fan-out, and a
small deflated stream with a deliberately large decoded text payload. Tests must
assert stable redacted error codes rather than native parser messages.
