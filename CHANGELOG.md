# CHANGELOG

<!-- version list -->

## v0.38.0 (2026-03-14)

### 📖 Documentation

- **messaging**: Update documentation for Critter Stack transition
  ([`3ab4e33`](https://github.com/waku-py/waku/commit/3ab4e33cbc4a68728256f44c652e8c627103429d))

### ♻️ Refactoring

- **messaging**: Remove IRequestHandler and IEventHandler protocols
  ([`3f428c8`](https://github.com/waku-py/waku/commit/3f428c814c574b0da9589e7e81281d597e034a6b))


## v0.37.0 (2026-03-12)

### ♻️ Refactoring

- **messaging**: Replace cqrs with messaging
  ([`4361f4f`](https://github.com/waku-py/waku/commit/4361f4fb73a2f60a7f9745b8d0763758c7746e78))

### 💥 Breaking Changes

- **messaging**: Waku.cqrs module replaced by waku.messaging. Mediator → MessageBus, IMediator →
  IMessageBus(ISender, IPublisher). ISender gains invoke() (request/response) + send()
  (fire-and-forget).


## v0.36.0 (2026-03-09)

### ✨ Features

- **es**: Add attempt context template method to handlers
  ([`4924485`](https://github.com/waku-py/waku/commit/4924485fa5cd31a5eb92b2eb845e309f9fa3ccfd))


## v0.35.1 (2026-03-07)

### 🪲 Bug Fixes

- **es**: Handle Union and generic alias types in resolve_generic_args
  ([`9bd9d02`](https://github.com/waku-py/waku/commit/9bd9d0205cc0accee8978a69c230710ab10b4071))


## v0.35.0 (2026-03-06)

### 📖 Documentation

- **core**: Use service/repository pattern in DI examples
  ([`a9e7b9d`](https://github.com/waku-py/waku/commit/a9e7b9d7dfad20ef5e603fbbdb4e7f65cb4404a6))

- **es**: Document cross-aggregate projections, live projections, and design decisions
  ([`604dfd2`](https://github.com/waku-py/waku/commit/604dfd247b89d220d061133cec42df6b6069dbc2))

### ✨ Features

- **es**: Propagate inline projection errors instead of swallowing
  ([`fb75e6f`](https://github.com/waku-py/waku/commit/fb75e6fd7db59a1f063e6323fa81c9de04fc469d))

- **es**: Split creation models for OOP aggregates and deciders
  ([`63c235a`](https://github.com/waku-py/waku/commit/63c235a166df8b1cb6a07d0e106f99d63c6f5d51))


## v0.34.1 (2026-03-03)

### 🪲 Bug Fixes

- **es**: Make sqla tables bind idempotent
  ([`138464e`](https://github.com/waku-py/waku/commit/138464eda83ca78da87af8a8e5a3f2e4de9a8c41))

### 📖 Documentation

- Add tags to all pages
  ([`2d46af6`](https://github.com/waku-py/waku/commit/2d46af6331e1dd5edb0ffbf066fe22c04c159b8d))

- Make `dishka` lowercase in docs
  ([`5235605`](https://github.com/waku-py/waku/commit/5235605b7bedffb2f9db0f2f35542c92c72f8ffb))

- Update all docs links to docs.wakupy.dev
  ([`44a39f7`](https://github.com/waku-py/waku/commit/44a39f721b98783804c77586117cdbcfcd034463))

- Update download badge and fix install commands
  ([`27dc0a9`](https://github.com/waku-py/waku/commit/27dc0a938c840035b62cf8aac4d6ac9ed6776290))

- **es**: Fill documentation gaps for event sourcing setup
  ([`5ac655c`](https://github.com/waku-py/waku/commit/5ac655cfcf6e66f8a315ca35b42b919125b5a919))


## v0.34.0 (2026-03-01)

### 📖 Documentation

- **es**: Fix inaccuracies and polish event sourcing docs
  ([`4217881`](https://github.com/waku-py/waku/commit/421788117d5144b6ea68671ca350d0758a6a2bb1))

### ✨ Features

- **es**: Add stream soft delete
  ([`e37ad72`](https://github.com/waku-py/waku/commit/e37ad72dea39effc6ae23d1086925a8a603529f3))


## v0.33.0 (2026-03-01)

### ✨ Features

- **es**: Add AggregateSpec, wait utilities, and gap detection
  ([`321cc03`](https://github.com/waku-py/waku/commit/321cc037aeb6bd5aeb0b38b37af69aeefab5588d))


## v0.32.0 (2026-03-01)

### 📖 Documentation

- **es**: Review and polish event sourcing documentation
  ([`2a1f2cb`](https://github.com/waku-py/waku/commit/2a1f2cb6708cdf6cf689df053a3efb9d2aa26d96))

- **es**: Update projection docs to use bind_catch_up_projection() API
  ([`eb9bea5`](https://github.com/waku-py/waku/commit/eb9bea581407173ce4680c556f77792997ae9a4e))

### ✨ Features

- **es**: Add event type filtering to read_all() and catch-up projections
  ([`595a42a`](https://github.com/waku-py/waku/commit/595a42a0af076339544d5f6fb4bd245ce39a2d68))

- **es**: Add optimistic concurrency retry to command handlers
  ([`636312c`](https://github.com/waku-py/waku/commit/636312ce71df4a87591d8253de898e1fd5e2bd92))


## v0.31.0 (2026-02-26)

### 🪲 Bug Fixes

- **es**: Use stream position for version and isolate inline projection failures
  ([`4e11aa4`](https://github.com/waku-py/waku/commit/4e11aa479a135b648a10e09e57e9df8ccd22cf5c))

- **es**: Warn on InMemoryEventStore default and make snapshot save best-effort
  ([`b4e6500`](https://github.com/waku-py/waku/commit/b4e6500cc1519d5bd0116a6aa8447fbd59023d69))

### 📖 Documentation

- Improve projection, aggregate, DI, and event documentation
  ([`f4fec74`](https://github.com/waku-py/waku/commit/f4fec7445366bdbdb400b71c8d740580c465d2e0))

- **es**: Document at-least-once projection semantics and outbox pattern
  ([`fc80584`](https://github.com/waku-py/waku/commit/fc80584cfbe734a38103bb8f3a9920d9bb2b19ff))

### ✨ Features

- **es**: Add snapshot_state_type override and fix projection retry counter
  ([`742d3cb`](https://github.com/waku-py/waku/commit/742d3cb90bf50b168f4b6dbcf9fe99bc5fc50b1e))

- **es**: Add structured logging and generic StoredEvent
  ([`422bb88`](https://github.com/waku-py/waku/commit/422bb88b837c155b42578aea48a3b7556f69ac3a))

- **es**: Always require passing event store impl class
  ([`c7d2d0a`](https://github.com/waku-py/waku/commit/c7d2d0a8e34f27868b9ecd2aed082422303f7e67))

- **es**: Refactor error policy to binding-level config with `on_skip` hook
  ([`0a99787`](https://github.com/waku-py/waku/commit/0a99787e62a0d1fc9b1da94987aca7020b5d5311))


## v0.30.0 (2026-02-18)

### 🪲 Bug Fixes

- **core**: Fix build phase mutations for multiple factory calls in single process
  ([`08b6f17`](https://github.com/waku-py/waku/commit/08b6f17bc50c696aab57821ddf8d1008b82dd8a6))

- **es**: Figure out idempotency issues
  ([`dc99ef7`](https://github.com/waku-py/waku/commit/dc99ef7ee13ef8fd5f366859a8855c67c6013b60))

- **es**: Remove invariants protection dead branches
  ([`b30e469`](https://github.com/waku-py/waku/commit/b30e469956bc23b0b54a97807c8baa8acc79de12))

### 📖 Documentation

- Add ask context7 widget
  ([`1288bea`](https://github.com/waku-py/waku/commit/1288bea6bc92c857e5cb17b75c9149f011ff0011))

- Refine introduction to have selling points section
  ([`3991e27`](https://github.com/waku-py/waku/commit/3991e2771d065ac993b0b6ddc25586310bae45fd))

- Refine introduction with better structure
  ([`5b21690`](https://github.com/waku-py/waku/commit/5b2169048a59ff937ee4c7a19a1a58d40fa9c575))

- **es**: Reflect idempotency and schema versioning
  ([`bca5806`](https://github.com/waku-py/waku/commit/bca5806887ca2ff7d1f7dabc4e27f20ce635cd81))

### ✨ Features

- **es**: Add idempotency handling to event sourcing events stores
  ([`feb9e05`](https://github.com/waku-py/waku/commit/feb9e051b089bcc9d5035e9ce47bc12f2c9231cb))

- **es**: Add snapshot schema versioning and large stream guard
  ([`e25369a`](https://github.com/waku-py/waku/commit/e25369a05cb905ada7d7e9f0d8fc4aee13d55217))


## v0.29.0 (2026-02-16)

### ✨ Features

- **ext**: Add event sourcing extension
  ([`e69f220`](https://github.com/waku-py/waku/commit/e69f220e6bc5dae71a59ccb1428e4c3f563e9283))


## v0.28.1 (2026-02-13)

### 🪲 Bug Fixes

- Adjust cqrs `many` to run `collect` only after all providers registered
  ([`92a2a24`](https://github.com/waku-py/waku/commit/92a2a24134f2e94802af35745cb40ac139562fa7))


## v0.28.0 (2026-02-12)

### ✨ Features

- Use dishka 1.8.0 features
  ([`08fe7cb`](https://github.com/waku-py/waku/commit/08fe7cbdd2bc5f3f554f8e50ab5d147e342786c0))


## v0.27.0 (2025-12-29)

### ✨ Features

- Migrate cqrs to `OnModuleRegistration` extension
  ([`18e4234`](https://github.com/waku-py/waku/commit/18e4234d088d6e55336fcb0ae63d00cf57f54af6))

- Rename base cqrs event marker to notification
  ([`5aa4d2f`](https://github.com/waku-py/waku/commit/5aa4d2f24528ce07851561e1b156cf764c43afe4))

- **tests**: Add app_extensions param to create_test_app
  ([`ffdba15`](https://github.com/waku-py/waku/commit/ffdba1546a32985e26d8465d86d11e26b9a5d047))


## v0.26.1 (2025-12-08)

### 🪲 Bug Fixes

- Add app_extensions param to create_test_app
  ([`3bba1ac`](https://github.com/waku-py/waku/commit/3bba1ac8a0276bc04434fc09aa61dd68e4954086))


## v0.26.0 (2025-12-08)

### ✨ Features

- Add `OnBeforeContainerBuild` extension
  ([`8803bce`](https://github.com/waku-py/waku/commit/8803bce78843ea3af1dec069d9d81230b4ae0068))


## v0.25.0 (2025-12-02)

### ✨ Features

- Add context override support to `override` helper
  ([`30e572f`](https://github.com/waku-py/waku/commit/30e572f280c3f42f0f789941247ba4d56442ea9f))


## v0.24.1 (2025-12-01)

### ⚡ Performance Improvements

- **cqrs**: Use iterative pipeline execution to avoid partial chains
  ([`d98ee30`](https://github.com/waku-py/waku/commit/d98ee301f52e56a21b40f85a956622e771fbb89e))


## v0.24.0 (2025-12-01)

### ✨ Features

- **cqrs**: Make event publishing noop when no handlers registered
  ([`ae73c04`](https://github.com/waku-py/waku/commit/ae73c041e4251d4732459a3898a82ed444610503))


## v0.23.0 (2025-11-29)

### ✨ Features

- Add conditional provider activation
  ([`a9aee1d`](https://github.com/waku-py/waku/commit/a9aee1df1bef5d551bbde9d62d56a088bb044dc3))


## v0.22.2 (2025-11-29)

### 🪲 Bug Fixes

- Adjust cqrs handlers typing to support ty v0.0.1a29
  ([`526102c`](https://github.com/waku-py/waku/commit/526102cfb28760c5963063f1609d5cb5489bd00b))


## v0.22.1 (2025-11-29)

### 🪲 Bug Fixes

- Do not trigger error on generic re-exports in modules
  ([`e1a5b56`](https://github.com/waku-py/waku/commit/e1a5b5602f6cff066f84fb6f0003a179603d0e35))


## v0.22.0 (2025-11-27)

### 🪲 Bug Fixes

- Allow usage of factories in many provider
  ([`bbc50a3`](https://github.com/waku-py/waku/commit/bbc50a318f34d36c759de2d2184a15e9ee63df92))

### ⚙️ Build System

- Add python 3.14.0rc1 to testing suite
  ([`442076f`](https://github.com/waku-py/waku/commit/442076f4d5cb467213a7c1eb6a2141ee5c4a5046))

### ✨ Features

- Make cqrs related handlers calls positional only
  ([`386d31a`](https://github.com/waku-py/waku/commit/386d31a6ac548ae92d9d735ca2d58cb8a84bcab5))


## v0.21.0 (2025-07-20)

### ✨ Features

- Improve providers registration interfaces
  ([`c43f2b8`](https://github.com/waku-py/waku/commit/c43f2b8892d48a10f199b64da0ff005050e5cd64))


## v0.20.0 (2025-06-11)

### ✨ Features

- **tests**: Make overrode container use strict validation
  ([`4089e58`](https://github.com/waku-py/waku/commit/4089e58e37ad5ccf86bd834e4040fe0b9057d763))


## v0.19.0 (2025-06-07)

### 📖 Documentation

- Further improve README
  ([`f4272c6`](https://github.com/waku-py/waku/commit/f4272c69abaa97e1b4d31cab1983ac7ff0a9b0fa))

- Improve readme
  ([`4e19320`](https://github.com/waku-py/waku/commit/4e19320c25eac2efe712e5ada0c71a21fcfd3f86))

- Update examples and documentation to reflect dishka migration
  ([`3ce62e0`](https://github.com/waku-py/waku/commit/3ce62e04a24f164816370d6920ba26a1f80939e2))

### ✨ Features

- **tests**: Allow to override not only application container
  ([`0d1f61e`](https://github.com/waku-py/waku/commit/0d1f61ec612e6f8f5f455d1337a973be89c57a22))


## v0.18.0 (2025-05-12)

### ⚙️ Build System

- Introduce astral-sh `ty` as additional typechecker
  ([`4eb9758`](https://github.com/waku-py/waku/commit/4eb97581cb4394f6f466df379d30969bc467ce30))

### 📖 Documentation

- Fix `markdownlint` issues in README and CONTRIBUTING files
  ([`cd3821e`](https://github.com/waku-py/waku/commit/cd3821e79cf08d6728c9297d03366f9e4b55fa2a))

### ✨ Features

- **ext**: Implement extension registry
  ([`6e88840`](https://github.com/waku-py/waku/commit/6e88840338ea53d31e3fd7565df1869993bc2c00))


## v0.17.0 (2025-05-11)

### ✨ Features

- **cqrs**: Improve cqrs subsystem with pipeline behaviors
  ([`8b84864`](https://github.com/waku-py/waku/commit/8b84864e38302b9837803d8bd395c2232e73a813))

### 💥 Breaking Changes

- **cqrs**: `mediator` package renamed to `cqrs`


## v0.16.0 (2025-05-04)

### ✨ Features

- **core**: Group all module providers within one dishka provider
  ([`4fa6850`](https://github.com/waku-py/waku/commit/4fa685036b6b794a26564ac9f66ecbf1ea32c289))


## v0.15.0 (2025-05-02)

### ✨ Features

- **validation**: Implement proper module re-export validation
  ([`f5e608b`](https://github.com/waku-py/waku/commit/f5e608b8835b24124564231e1a45d57ac5961f4c))


## v0.14.0 (2025-04-30)

### ✨ Features

- **validation**: Make dependency accessible rule stricter and follow nestjs behaviour
  ([`574c453`](https://github.com/waku-py/waku/commit/574c45380aaed20bad8ea44f0dfecf9863767790))


## v0.13.0 (2025-04-30)

### ✨ Features

- Implement proper module hooks sorting
  ([`2b2a9de`](https://github.com/waku-py/waku/commit/2b2a9de832b3890c21cf9dfcccfd3f30802f3761))


## v0.12.1 (2025-04-29)

### ⚡ Performance Improvements

- **core**: Collect module adjacency in one pass
  ([`9ad711d`](https://github.com/waku-py/waku/commit/9ad711d4454c0963c806cacc682b5059f687a3e3))


## v0.12.0 (2025-04-28)

### ✨ Features

- **core**: Make module traversal post-order dfs
  ([`eb9a760`](https://github.com/waku-py/waku/commit/eb9a7606c682f8d2b6dbd9e78ba3a7ef21ad150f))


## v0.11.3 (2025-04-28)

### 🪲 Bug Fixes

- **core**: Move `OnModuleConfigure` to module definition to prevent duplications on subsequent
  calls
  ([`0e6ed00`](https://github.com/waku-py/waku/commit/0e6ed004fef7357325bc7f8b5aab5765da763821))


## v0.11.2 (2025-04-28)

### 🪲 Bug Fixes

- **validation**: Fix check for provided types in the current module without explicit export
  ([`a771cbb`](https://github.com/waku-py/waku/commit/a771cbb6615ce4223ad947f7751f4a6a2faf286b))


## v0.11.1 (2025-04-28)

### ⚡ Performance Improvements

- Remove duplications in module registry builder logic
  ([`8527c7d`](https://github.com/waku-py/waku/commit/8527c7deccdf985dad0ef9eb7cb9c3fe8fd43fa2))


## v0.11.0 (2025-04-28)

### 📖 Documentation

- Enhance contributing guide and README
  ([`6e4ddda`](https://github.com/waku-py/waku/commit/6e4dddabc98d694aadddda5141a8b698571f9868))

- Fix issue with mediatr link in readme
  ([`8435d2f`](https://github.com/waku-py/waku/commit/8435d2f16ccf01969063ee5fff4755171a79c859))

- Improve api reference docs generation; document override helper
  ([`48061d0`](https://github.com/waku-py/waku/commit/48061d0cb325c800492d434f815de272427badd2))

- Improve feature descriptions in README
  ([`2d18bee`](https://github.com/waku-py/waku/commit/2d18beeb3545e7ff910e645ce366d342cd0c565c))

- Update README for clarity
  ([`efaf3d5`](https://github.com/waku-py/waku/commit/efaf3d5a59c31b78d516a3df3a50e2f52542d567))

### ✨ Features

- **core**: Replace ModuleGraph with ModuleRegistry for module management
  ([`f807f0a`](https://github.com/waku-py/waku/commit/f807f0a406cb208083c56780da9f2d768d2bb34c))

### 💥 Breaking Changes

- **core**: All module graph operations now use `ModuleRegistry`; direct usage of `ModuleGraph` is
  no longer supported. Update custom extensions and validation logic accordingly.


## v0.10.0 (2025-04-26)

### ✨ Features

- Re-export di related object from dishka
  ([`820ddc4`](https://github.com/waku-py/waku/commit/820ddc48512ac2a1ffde6b397980c61844d5e7b3))


## v0.9.1 (2025-04-26)

### ⚡ Performance Improvements

- Optimize DependenciesAccessible rule
  ([`8eb1ae2`](https://github.com/waku-py/waku/commit/8eb1ae26a24982d4d9482b1117ad9ef7f6b2105d))


## v0.9.0 (2025-04-25)

### 🪲 Bug Fixes

- Skip context providers when validation that deps accessible
  ([`12d658e`](https://github.com/waku-py/waku/commit/12d658e855286e8aba7558777e15e78ab839993f))

- Trigger release
  ([`2d31df1`](https://github.com/waku-py/waku/commit/2d31df11db3ca161a24271efaa14de754b96fcca))

### 📖 Documentation

- Actualize readme file
  ([`1001875`](https://github.com/waku-py/waku/commit/10018751c8b7a9640042301e27eff9251fd7c965))

- Fix readme formatting
  ([`116e424`](https://github.com/waku-py/waku/commit/116e424ea3709d434de0728a9d05037a4bb24aae))

### ✨ Features

- Add container override helper to application
  ([`1e2f216`](https://github.com/waku-py/waku/commit/1e2f2162e185a74f28ef6b3986c52171b3b59412))

- Add di helpers, add testing override helper
  ([`66d7bb5`](https://github.com/waku-py/waku/commit/66d7bb5ea2ff4aa4fa57ad04666a96994a04c03f))


## v0.8.0 (2025-04-24)

### 📖 Documentation

- Improve readme, add motivation section
  ([`059e5c9`](https://github.com/waku-py/waku/commit/059e5c94169388f744f0f76b5f77352fa1e3cd7c))

### ✨ Features

- Migrate to dishka as ioc provider
  ([`7296122`](https://github.com/waku-py/waku/commit/7296122524eaa6ef172582bcfa4f003d6af46640))


## v0.7.0 (2025-04-13)

### ✨ Features

- Rename application to waku
  ([`12c9cd8`](https://github.com/waku-py/waku/commit/12c9cd872de32d4ba38d6b0fafeb66c22cb1c6d0))


## v0.6.0 (2025-03-25)

### 🪲 Bug Fixes

- Change links to di docs in readme
  ([`0bf7086`](https://github.com/waku-py/waku/commit/0bf7086627171b0cd2d62560f375a06d7b21f3de))

### 📖 Documentation

- Rename development section to contributing ([PR#19](https://github.com/waku-py/waku/pull/19),
  [`08ec5f5`](https://github.com/waku-py/waku/commit/08ec5f508e696a52a25aca1051a3868421441d18))

- Rename di section to providers, improve its contents
  ([PR#111](https://github.com/waku-py/waku/pull/111),
  [`f63c936`](https://github.com/waku-py/waku/commit/f63c9364750668fd4d6563a276555d1b791f71cd))

- Slightly reorganize documentation ([PR#19](https://github.com/waku-py/waku/pull/19),
  [`fe8220a`](https://github.com/waku-py/waku/commit/fe8220a4aed9a82f4ee28977a90ad2e6312781ad))

- **di**: Expand di documentation ([PR#111](https://github.com/waku-py/waku/pull/111),
  [`887e76f`](https://github.com/waku-py/waku/commit/887e76f84206cefa168a2852701f3adb38c1bc29))

### ✨ Features

- Add more info to provider validation error messages
  ([PR#113](https://github.com/waku-py/waku/pull/113),
  [`9dced06`](https://github.com/waku-py/waku/commit/9dced064b9892cdd4fa98a892b7108f3753bc9dd))


## v0.5.0 (2025-03-23)

### 📖 Documentation

- Add basic modules and providers docs
  ([`9ddbc3b`](https://github.com/waku-py/waku/commit/9ddbc3b78cae401bf72b02f4541bb3f92b70bbcd))

- Further improve documentation #19 #94
  ([`3d061b6`](https://github.com/waku-py/waku/commit/3d061b69d2321aaa925f939d25de81c2b7a96dc0))

### ✨ Features

- **mediator**: Simplify cqrs handlers registration (#105)
  ([PR#107](https://github.com/waku-py/waku/pull/107),
  [`65fa07d`](https://github.com/waku-py/waku/commit/65fa07d9fcbb685ad9fb7ec8a37e878aa06b5c84))


## v0.4.0 (2025-03-16)

### 📖 Documentation

- Setup mkdocs and improve documentation
  ([`d5166d7`](https://github.com/waku-py/waku/commit/d5166d7146b41a2ce18fba6e524008df2c694299))

### ✨ Features

- Add ability to pass custom context to dependency provider
  ([`a0e055f`](https://github.com/waku-py/waku/commit/a0e055f3ae5ca5f4cf6529bbf0026db6bce6f530))


## v0.3.1 (2025-02-13)

### 🪲 Bug Fixes

- Do not unwrap generic annotations in `collect_dependencies`
  ([`c5f1b7c`](https://github.com/waku-py/waku/commit/c5f1b7c18ce44d2c30ca0956b07a0e74ecb0474c))


## v0.3.0 (2025-02-10)

### ✨ Features

- Refactor module system
  ([`43a102a`](https://github.com/waku-py/waku/commit/43a102a1e39bb46a2bfd46f3d07c8b34aef39e9c))


## v0.2.0 (2025-01-12)

### 📖 Documentation

- Add docstrings to mediator related stuff
  ([`10728c0`](https://github.com/waku-py/waku/commit/10728c072bc191575aa5183efa74977df9dd66db))

- Deploy mkdocs to github pages #17
  ([`23872d7`](https://github.com/waku-py/waku/commit/23872d7d4fbf852dbff31dd1c9483a2fd91bb600))

- Improve readme and contributing guide
  ([`93529d1`](https://github.com/waku-py/waku/commit/93529d1185b0cf74eae2f8af22c5632d6a4eaab0))

- Trigger docs deploy #17
  ([`096876f`](https://github.com/waku-py/waku/commit/096876f6f08fc361e0bf2ac2517186b37c176acd))

- Use org bot for pages deploy #17
  ([`dfd1786`](https://github.com/waku-py/waku/commit/dfd17863cf5949abe19157f538b6be77f0bf4f31))

### ✨ Features

- Eliminate maps usage in mediator
  ([`16679d7`](https://github.com/waku-py/waku/commit/16679d7a87f6f90b84c9768105b340113d505280))

- Refactor mediator extension, add events handling support
  ([`3441375`](https://github.com/waku-py/waku/commit/3441375c67c7947b9194f49c2b2bd4d895b0a2f6))


## v0.1.6 (2024-12-20)

### 🪲 Bug Fixes

- Skip ci in release commit
  ([`9adbc58`](https://github.com/waku-py/waku/commit/9adbc582057fe452f742373226f4ec694b34b589))


## v0.1.5 (2024-12-20)

### 🪲 Bug Fixes

- Remove unused version var from init, run gitlint only for prs
  ([`7a270d4`](https://github.com/waku-py/waku/commit/7a270d4929d97d06a0482e85680215e38672b39f))


## v0.1.4 (2024-12-20)

### 🪲 Bug Fixes

- Attempt to fix contextvar from different context error
  ([`d98c2f8`](https://github.com/waku-py/waku/commit/d98c2f892bed26879e0e98db4f294f63cf9569bc))


## v0.1.3 (2024-12-20)

### 🪲 Bug Fixes

- **ext**: Make mediator middlewares work
  ([`4e4a593`](https://github.com/waku-py/waku/commit/4e4a593060133cd865e7bd7e1f0ae6c9c4af10f3))

### ⚙️ Build System

- Make patch release only for `fix` & `perf` tags
  ([`b9cf6bf`](https://github.com/waku-py/waku/commit/b9cf6bf77047e21959f4e1bbe2a1bb1566cccd1c))

### 📖 Documentation

- Add readme and contributing guide
  ([`32043d7`](https://github.com/waku-py/waku/commit/32043d7f03ba9b34cfc3d70809982643a876a999))


## v0.1.2 (2024-12-19)


## v0.1.1 (2024-12-19)

### ✨ Features

- **validation**: Add ValidationRule protocol, implement DIScopeMismatch
  ([`4cfdd23`](https://github.com/waku-py/waku/commit/4cfdd23354f42855f985713f440fe8cba9351a4a))


## v0.1.0 (2024-12-18)

- Initial Release
