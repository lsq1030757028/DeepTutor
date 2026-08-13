"""Built-in capability class paths."""

BUILTIN_CAPABILITY_CLASSES: dict[str, str] = {
    "chat": "deeptutor.agents.chat.capability:ChatCapability",
    "deep_solve": "deeptutor.capabilities.solve.capability:DeepSolveCapability",
    "deep_question": "deeptutor.agents.question.capability:DeepQuestionCapability",
    "deep_research": "deeptutor.agents.research.capability:DeepResearchCapability",
    "math_animator": "deeptutor.agents.math_animator.capability:MathAnimatorCapability",
    "visualize": "deeptutor.agents.visualize.capability:VisualizeCapability",
    "mastery_path": "deeptutor.capabilities.mastery.capability:MasteryPathCapability",
    # [fork] 测试旅程载体（决策 0019 案 B）。放末尾：上游新增 capability 也在此追加，
    # 行级冲突可自动合。
    "test": "deeptutor.agents.test.capability:TestCapability",
}
