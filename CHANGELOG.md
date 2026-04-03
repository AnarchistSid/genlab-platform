# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-04

### Added
- Initial public release
- genlab-core shared library: pipeline runner, platform clients (Instagram, YouTube, Facebook, X, Threads, TikTok), learning loop (LinUCB bandit), engagement engine, video compositor
- 5 channel configurations: AI creators, gaming, sports, movies, anime
- Operations dashboard (React + Flask) with approval workflow
- Affiliate monetization engine with multi-network support
- 23 automated services via launchd
- MIT License

### Security
- Secret scanning via gitleaks (pre-commit + CI)
- CSRF protection on all authenticated endpoints
- Parameterized SQL queries throughout
- Bot disclosure on AI-generated engagement replies
