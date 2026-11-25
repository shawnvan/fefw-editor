"""Keep the Flags page and generated name dictionary on the same catalogues."""
import audiences
import boons
import deeds
import fast_travel
import facilities
import facility_access
import fefw_core as core
import tutorials
import unit_entries

KNOWN_FLAGS = {
    0xb9a0e16b: ('禁止_武器屋', 'Disabled: Armorer'),
    0x5e9748e0: ('禁止_道具屋', 'Disabled: Item shop'),
    0x3fdd2f30: ('神殿_転移_カーラ神殿', 'Registered: Gate of the Gods: Kalla'),
    0x63fc6b22: ('機能開放_厩舎', 'Facility unlocked: Stables'),
    0xb78f6fac: ('衣装_イオ', 'Outfit preference: Io'),
    0xdfc64df2: ('衣装_ウルタンド', 'Outfit preference: Ultand'),
    0x817d9135: ('衣装_ウーシュラ', 'Outfit preference: Ursula'),
    0xc334c02c: ('衣装_エスメラルダ', 'Outfit preference: Esmeralda'),
    0x9cb7d49b: ('衣装_オリンピア', 'Outfit preference: Olympia'),
    0xc412e7ec: ('衣装_カターニャ', 'Outfit preference: Catania'),
    0x88ccd056: ('衣装_ザーコネ', 'Outfit preference: Zarcone'),
    0xba32f3ec: ('衣装_ジャスミン', 'Outfit preference: Jasmine'),
    0x8f0fcae7: ('衣装_セオドラ', 'Outfit preference: Theodora'),
    0x9ac07b22: ('衣装_セテス', 'Outfit preference: Seteth'),
    0xad139dc2: ('衣装_ソフィア', 'Outfit preference: Sofia'),
    0xed38a065: ('衣装_ダンテ', 'Outfit preference: Dante'),
    0x7b7b970a: ('衣装_ナジャ', 'Outfit preference: Nezha'),
    0xf997526c: ('衣装_ニネ', 'Outfit preference: Ninae'),
    0x24596c0f: ('衣装_ノクチュラ', 'Outfit preference: Noctula'),
    0x68a9f181: ('衣装_ピーテル', 'Outfit preference: Peter'),
    0x1524f058: ('衣装_ベンディッツ', 'Outfit preference: Benditz'),
    0x98b49461: ('衣装_ミカエラ', 'Outfit preference: Mikaela'),
    0x3bb88c81: ('衣装_ムウ', 'Outfit preference: Mu'),
    0xeed9b2d0: ('衣装_リリアン', 'Outfit preference: Lilian'),
    0xc7911d6b: ('衣装_着せ替え無効', 'Outfit changes disabled'),
}


def apply_labels(names: dict[int, dict], *, strict: bool = True) -> None:
    for h, (key, label) in KNOWN_FLAGS.items():
        if core.id_hash(key) != h or names.get(h, {}).get('name', key) != key:
            if strict:
                raise core.SaveError(f'Conflicting known flag identity for {h:08x}')
            continue
        names[h] = {**names.get(h, {}), 'name': key, 'label': label}
    for module in (tutorials, fast_travel, audiences, deeds, boons, facilities, facility_access, unit_entries):
        # A bad catalogue must not leave a partial overlay behind. The Flags
        # page can still display raw records; catalogue pages report errors.
        updated = dict(names)
        try:
            module.apply_labels(updated)
        except core.SaveError:
            if strict:
                raise
        else:
            names.update(updated)
