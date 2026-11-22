# F03 negative kernel cases

The cases in `cases.json` come from the written F03 `contract.md` and the
brief's later decisions. The test builds a supported plan for each backend,
applies the listed mutation, and checks the first refusal reason and
diagnostic in both `supports` and `build`. Native probes and evaluations are
guarded so any call during a refusal fails the test.

The same test module covers the distinct unavailable and precision outcomes,
factory dispatch, mock record validation, and byte limits. The PicoGK
inspection boundary uses a small bytes subclass with a controlled length to
exercise the configured limit without allocating a full-size STL.

## Contract disagreements

None identified in the covered cases during source review.
