# Pattern discovery

Pattern candidates come with `support` (symbols that follow it) and `violations` (counter-examples).
Confidence is the Wilson lower bound of support over support + violations, times a source weight,
so a single counter-example visibly lowers it.

## Judging a candidate

1. Read three supporting examples. Is the claim true for them, stated precisely?
2. Read every counter-example. Classify each:
   - a real violation (legacy shortcut, bug): the pattern holds; say so and suggest fixing the code;
   - a legitimate exception (composition root, test helper): the claim needs narrowing;
   - noise (misclassified role): the pattern holds; note the misclassification.
3. Recommend approve when the claim is true and useful to future work. Recommend reject when it is
   accidental (two classes that happen to look alike) or too vague to guide anyone.

## Lifecycle patterns

"X lifecycle is owned by Y" means one class owns a multi-step lifecycle (create → validate → consume
→ expire). These are the most valuable reuse signals: future features needing the lifecycle should
extend Y. Confirm that every listed user really goes through Y.
