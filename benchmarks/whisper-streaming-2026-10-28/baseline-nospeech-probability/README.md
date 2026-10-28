# BOS no-speech probability experiment

The second image retained all eight normal/quiet spoken acknowledgements, but did not suppress hallucinated trailing acknowledgements in the two FLEURS smoke recordings. Its raw BOS no-speech probability rounded to0.000000 for every tested input, including digital-silence warmup. Therefore the0.6 gate was not effective for this deployed checkpoint and this image was not activated in production. See diagnostics.json and the preserved raw outputs.
