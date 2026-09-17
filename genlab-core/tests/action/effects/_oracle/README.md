# Frozen pre-port oracle — CONTENT-17 v6 (operator-approved 2026-09-16)

`v6_look.py` and `v8_look.py` are copied VERBATIM from the approved deliverable
`.deliverables/clutchwire_action_hit3s_v6/scripts/`. They are the implementation
that produced `clutchwire_action_hit3s_v6.mp4`, the segment the operator signed
off on. They are here, in-tree, so `test_port_equivalence.py` can prove that
`genlab_core.action.effects.impact` computes the same thing.

Do not edit them. They are not a library; they are a fixed point to measure
against. If an effect must change, change `impact.py` and record the intended
delta in the equivalence test's tolerance with the reason.

WHY VENDORED: the packet's stated port gate was "re-render the WWE hit3s through
the module, PSNR >= 35 dB vs approved v6". That gate is unrunnable -- its inputs
(the WWE source clip and the 96 SAM2 per-instance mattes) lived in an ephemeral
scratch dir that has since been cleaned, and the crop script imports `v4_build`
which was never archived. Function-level equivalence is the stricter instrument
anyway: PSNR 35 dB tolerates visible differences, this tolerates ~none. Vendoring
is what stops the replacement gate from rotting the same way.
