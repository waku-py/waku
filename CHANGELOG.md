# Changelog

<!-- version list -->

## v0.7.0 (2025-04-13)

### Features

- Rename application to waku
  ([`12c9cd8`](https://github.com/waku-py/waku/commit/12c9cd872de32d4ba38d6b0fafeb66c22cb1c6d0))


## v0.6.0 (2025-03-25)

### Bug Fixes

- Change links to di docs in readme
  ([`0bf7086`](https://github.com/waku-py/waku/commit/0bf7086627171b0cd2d62560f375a06d7b21f3de))

### Documentation

- Rename development section to contributing ([#19](https://github.com/waku-py/waku/pull/19),
  [`08ec5f5`](https://github.com/waku-py/waku/commit/08ec5f508e696a52a25aca1051a3868421441d18))

- Rename di section to providers, improve its contents
  ([#111](https://github.com/waku-py/waku/pull/111),
  [`f63c936`](https://github.com/waku-py/waku/commit/f63c9364750668fd4d6563a276555d1b791f71cd))

- Slightly reorganize documentation ([#19](https://github.com/waku-py/waku/pull/19),
  [`fe8220a`](https://github.com/waku-py/waku/commit/fe8220a4aed9a82f4ee28977a90ad2e6312781ad))

- **di**: Expand di documentation ([#111](https://github.com/waku-py/waku/pull/111),
  [`887e76f`](https://github.com/waku-py/waku/commit/887e76f84206cefa168a2852701f3adb38c1bc29))

### Features

- Add more info to provider validation error messages
  ([#113](https://github.com/waku-py/waku/pull/113),
  [`9dced06`](https://github.com/waku-py/waku/commit/9dced064b9892cdd4fa98a892b7108f3753bc9dd))


## v0.5.0 (2025-03-23)

### Documentation

- Add basic modules and providers docs
  ([`9ddbc3b`](https://github.com/waku-py/waku/commit/9ddbc3b78cae401bf72b02f4541bb3f92b70bbcd))

- Further improve documentation #19 #94
  ([`3d061b6`](https://github.com/waku-py/waku/commit/3d061b69d2321aaa925f939d25de81c2b7a96dc0))

* add getting started section * add more usage docs * change api reference appearance * improve
  mkdocs config

### Features

- **mediator**: Simplify cqrs handlers registration (#105)
  ([#107](https://github.com/waku-py/waku/pull/107),
  [`65fa07d`](https://github.com/waku-py/waku/commit/65fa07d9fcbb685ad9fb7ec8a37e878aa06b5c84))

* Remove `MediatorProvidersCreator` * Add new module extension type `OnModuleConfigure` for allowing
  change module metadata before module creating * Move handlers binding logic to `MediatorExtension`

Resolves: #105


## v0.4.0 (2025-03-16)

### Documentation

- Setup mkdocs and improve documentation
  ([`d5166d7`](https://github.com/waku-py/waku/commit/d5166d7146b41a2ce18fba6e524008df2c694299))

### Features

- Add ability to pass custom context to dependency provider
  ([`a0e055f`](https://github.com/waku-py/waku/commit/a0e055f3ae5ca5f4cf6529bbf0026db6bce6f530))


## v0.3.1 (2025-02-13)

### Bug Fixes

- Do not unwrap generic annotations in `collect_dependencies`
  ([`c5f1b7c`](https://github.com/waku-py/waku/commit/c5f1b7c18ce44d2c30ca0956b07a0e74ecb0474c))


## v0.3.0 (2025-02-10)

### Features

- Refactor module system
  ([`43a102a`](https://github.com/waku-py/waku/commit/43a102a1e39bb46a2bfd46f3d07c8b34aef39e9c))


## v0.2.0 (2025-01-12)

### Documentation

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

### Features

- Eliminate maps usage in mediator
  ([`16679d7`](https://github.com/waku-py/waku/commit/16679d7a87f6f90b84c9768105b340113d505280))

- Refactor mediator extension, add events handling support
  ([`3441375`](https://github.com/waku-py/waku/commit/3441375c67c7947b9194f49c2b2bd4d895b0a2f6))

Resolves #12 Fixes #16


## v0.1.6 (2024-12-20)

### Bug Fixes

- Skip ci in release commit
  ([`9adbc58`](https://github.com/waku-py/waku/commit/9adbc582057fe452f742373226f4ec694b34b589))


## v0.1.5 (2024-12-20)

### Bug Fixes

- Remove unused version var from init, run gitlint only for prs
  ([`7a270d4`](https://github.com/waku-py/waku/commit/7a270d4929d97d06a0482e85680215e38672b39f))


## v0.1.4 (2024-12-20)

### Bug Fixes

- Attempt to fix contextvar from different context error
  ([`d98c2f8`](https://github.com/waku-py/waku/commit/d98c2f892bed26879e0e98db4f294f63cf9569bc))


## v0.1.3 (2024-12-20)

### Bug Fixes

- **ext**: Make mediator middlewares work
  ([`4e4a593`](https://github.com/waku-py/waku/commit/4e4a593060133cd865e7bd7e1f0ae6c9c4af10f3))

### Build System

- Make patch release only for `fix` & `perf` tags
  ([`b9cf6bf`](https://github.com/waku-py/waku/commit/b9cf6bf77047e21959f4e1bbe2a1bb1566cccd1c))

### Documentation

- Add readme and contributing guide
  ([`32043d7`](https://github.com/waku-py/waku/commit/32043d7f03ba9b34cfc3d70809982643a876a999))


## v0.1.2 (2024-12-19)

### Continuous Integration

- Fix semantic release step
  ([`c516322`](https://github.com/waku-py/waku/commit/c516322cdd49adddef51e30d35b730cc241c8300))

- Fix semantic release step 2
  ([`176a466`](https://github.com/waku-py/waku/commit/176a466a9e6d8787a7044c3b8ad64a4bd7acdf0a))

- Setup github actions pipelines
  ([`3f0ec58`](https://github.com/waku-py/waku/commit/3f0ec583b032e6c0a0cd1c1fd70b6eddfb33c34b))


## v0.1.1 (2024-12-19)

### Chores

- **deps**: Use litestar-msgspec with litestar extra on all python version
  ([`ef60b04`](https://github.com/waku-py/waku/commit/ef60b0403bd1be7a9991cab764f84de482d060fa))


## v0.1.0 (2024-12-18)

### Bug Fixes

- Module validation
  ([`45c58b1`](https://github.com/waku-py/waku/commit/45c58b1f9fc393e4e4e39a292d627a77f54fea76))

### Chores

- Adjust dev workflow, add semantic release config
  ([`ab3313a`](https://github.com/waku-py/waku/commit/ab3313a7a5ee6932edc7f7a17b57139d4fbc5553))

- **ci**: Try fix ci
  ([`2bf8383`](https://github.com/waku-py/waku/commit/2bf8383d1e1c3d45228c313ec73d3e9dfa138e65))

### Features

- Add providers registration, fix validation
  ([`35ef7f3`](https://github.com/waku-py/waku/commit/35ef7f3a3e1411428989d6bf6586e66c250f00e7))

- Add semantic release, rename package
  ([`a8addaf`](https://github.com/waku-py/waku/commit/a8addafc6f609b3f7895922e158b49b183d24bce))

- Implement di & mediator extension
  ([`f7ebdb9`](https://github.com/waku-py/waku/commit/f7ebdb9a567bf7c723916df1db62846eebe863f5))

- Improve app & di lifespan, add check for app providers
  ([`d5b3a31`](https://github.com/waku-py/waku/commit/d5b3a310d4a1ccf1f32b8ae14746b777d857893a))

- Improve providers validation & add imports to init files
  ([`7bd5999`](https://github.com/waku-py/waku/commit/7bd59994c99bc8300df1ffc654b21e27398e0425))

- Make application module itself, improve aioinject provider
  ([`9ae70a2`](https://github.com/waku-py/waku/commit/9ae70a2a7e35be314e4613c58bf185141049961b))
