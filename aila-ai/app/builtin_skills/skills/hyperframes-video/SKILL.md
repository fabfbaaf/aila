---
name: hyperframes-video
description: 把调研简报、讲稿、截图、图表、字幕或已有音频做成短视频，并在本机导出 MP4。适合技术解说、产品演示和更新日志视频。
---
# 用 HyperFrames 做短视频

1. 先读用户给的讲稿、简报和素材。做技术解说时，把核对过的来源留在片尾或随视频交付的 sources.md。调研还没做时，可先用 paper-lookup。
2. 按用户的要求确定画幅、时长和旁白。B 站演示默认横屏 16:9，明确要竖版时用 9:16。按镜头写出时间段、画面和字幕。已有音频时按它的时长对齐。
3. 在工作目录新建独立的视频工程文件夹，用 read_skill 读本技能的 examples/title-card.html，把 HTML 正文写成工程里的 index.html。用相对路径引用工程里的素材。多个镜头用 data-start、data-duration 和 data-track-index 对齐；动画按合成的时间轴驱动。更多格式见 [HyperFrames 合成格式](https://github.com/heygen-com/hyperframes/blob/4fca68b23561e3f1271b876ed4715dce9c7cf208/skills/hyperframes/references/format.md)。
4. 用 render_video 选这个工程并指定 MP4 文件名。这个工具会自动准备本地渲染依赖、检查画面和素材，再导出 MP4。Windows 首次使用要联网。检查没通过时按返回的问题改好工程再试。
5. 交付实际生成的视频路径、工程路径和素材来源，说明有无旁白和字幕。工程保留供后续改动。
