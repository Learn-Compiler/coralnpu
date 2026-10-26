"""Read ELF symbols and actual post-execution SRAM; never synthesize results."""
from pathlib import Path
import struct


def elf_symbols(path):
    data = Path(path).read_bytes()
    if len(data) < 52 or data[:6] != b'\x7fELF\x01\x01':
        raise ValueError('需要 ELF32 小端文件')
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    offset, entry_size, count = header[6], header[11], header[12]
    if not offset or not count:
        return {}
    if entry_size < 40 or offset + count * entry_size > len(data):
        raise ValueError('ELF 节表不完整')
    sections = [struct.unpack_from('<10I', data, offset + i * entry_size)
                for i in range(count)]
    symbols = {}
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        start, size, link, stride = section[4], section[5], section[6], section[9]
        if link >= count or stride < 16 or start + size > len(data):
            raise ValueError('ELF 符号表不完整')
        strings_section = sections[link]
        strings = data[strings_section[4]:strings_section[4] + strings_section[5]]
        for pos in range(start, start + size, stride):
            name, address, length, info, other, index = struct.unpack_from('<IIIBBH', data, pos)
            if not name or not index or name >= len(strings):
                continue
            end = strings.find(b'\0', name)
            if end < 0:
                raise ValueError('ELF 符号名未终止')
            text = strings[name:end].decode('utf-8', errors='replace')
            symbols.setdefault(text, []).append(dict(address=address, size=length))
    return symbols


class Memory:
    def __init__(self, path):
        data = Path(path).read_bytes()
        self.blocks = []
        pos = 0
        while pos < len(data):
            if pos + 12 > len(data):
                raise ValueError('SRAM dump 头部不完整')
            base, size = struct.unpack_from('<QI', data, pos)
            pos += 12
            if not size or pos + size > len(data):
                raise ValueError('SRAM dump 数据不完整')
            self.blocks.append((base, data[pos:pos + size]))
            pos += size

    def read(self, address, size):
        result = bytearray()
        while len(result) < size:
            current = address + len(result)
            for base, data in self.blocks:
                if base <= current < base + len(data):
                    offset = current - base
                    result.extend(data[offset:offset + size - len(result)])
                    break
            else:
                raise ValueError(f'SRAM dump 不包含地址 0x{current:x}')
        return bytes(result)


def get_symbol(symbols, name):
    matches = symbols.get(name, [])
    if len(matches) != 1:
        raise ValueError(f'符号 {name!r} 不存在或有歧义；请保留 ELF 符号表')
    return matches[0]


def inspect(elf, dump, requested, check_add=False):
    symbols, memory = elf_symbols(elf), Memory(dump)
    result = dict(arrays={}, profile=None, validation=None)
    if '__coral_profile' in symbols:
        symbol = get_symbol(symbols, '__coral_profile')
        fields = struct.unpack('<10I', memory.read(symbol['address'], 40))
        if fields[0] == 0x43505231:
            completed = fields[9] == 1
            result['profile'] = dict(
                completed=completed, main_return=fields[5] if completed else None,
                cycles=((fields[3] - fields[1]) & 0xffffffff) if completed else None,
                retired_instructions=((fields[4] - fields[2]) & 0xffffffff) if completed else None,
                scope='main interval including measurement overhead',
                mcause=fields[6], mepc=fields[7], mtval=fields[8])
    formats = {'u32': 'I', 'i32': 'i', 'f32': 'f'}
    for spec in requested:
        parts = spec.rsplit(':', 1)
        name, dtype = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], 'u32')
        if dtype not in formats:
            raise ValueError('数据类型只支持 u32、i32、f32')
        symbol = get_symbol(symbols, name)
        size = symbol['size']
        if not size or size % 4 or size > 32768:
            raise ValueError(f'{name} 的符号大小不是受支持的 32 bit 数组：{size}')
        values = list(struct.unpack('<' + formats[dtype] * (size // 4),
                                    memory.read(symbol['address'], size)))
        result['arrays'][name] = dict(**symbol, dtype=dtype, values=values)
    if check_add:
        arrays = result['arrays']
        if not all(x in arrays and arrays[x]['dtype'] == 'u32' for x in ('a', 'b', 'c')):
            raise ValueError('add_u32 校验需要 --show a:u32 --show b:u32 --show c:u32')
        a, b, c = (arrays[x]['values'] for x in ('a', 'b', 'c'))
        if not (len(a) == len(b) == len(c)):
            raise ValueError('add_u32 输入和结果长度不一致')
        expected = [(x + y) & 0xffffffff for x, y in zip(a, b)]
        mismatches = [i for i, (x, y) in enumerate(zip(c, expected)) if x != y]
        result['validation'] = dict(kind='add_u32', count=len(c),
                                    passed=not mismatches, expected=expected,
                                    mismatches=mismatches)
    return result


def display(result, limit=32):
    profile = result.get('profile')
    if profile:
        if profile['completed']:
            print(f"[程序返回] main() = {profile['main_return']}", flush=True)
            print(f"[main 区间] {profile['cycles']} cycles，"
                  f"{profile['retired_instructions']} 条退休指令（含测量开销）", flush=True)
        else:
            print('[程序异常] main 未正常返回；'
                  f"mcause=0x{profile['mcause']:x}, mepc=0x{profile['mepc']:x}, "
                  f"mtval=0x{profile['mtval']:x}", flush=True)
    else:
        print('[计数] 此 ELF 没有 __coral_profile，无法提供 main 区间计数', flush=True)
    arrays = result.get('arrays', {})
    validation = result.get('validation')
    if validation:
        a, b, c = (arrays[x]['values'] for x in ('a', 'b', 'c'))
        print('\n[RTL SRAM 实际数据] uint32 向量加法：c = a + b', flush=True)
        print(' index          a          b   actual_c   expected    check', flush=True)
        for i in range(min(len(c), limit)):
            expected = validation['expected'][i]
            print(f" {i:5d} {a[i]:10d} {b[i]:10d} {c[i]:10d} {expected:10d}    "
                  + ('PASS' if c[i] == expected else 'FAIL'), flush=True)
        print(f"[逐元素校验] {len(c) - len(validation['mismatches'])}/{len(c)} 通过", flush=True)
    else:
        for name, array in arrays.items():
            print(f"[SRAM] {name} @ 0x{array['address']:08x} ({array['dtype']}): "
                  f"{array['values'][:limit]}", flush=True)
    if not arrays:
        print('[数据] 需要观察数组时使用 --show 符号名:u32（也支持 i32/f32）', flush=True)


def write_report(path, result):
    sim = result.get('simulation') or {}
    data = result.get('feedback') or {}
    lines = ['# Coral 单核仿真结果', '',
             f"- 状态：**{result['status']}**，包装入口退出码 {result['exit_code']}，原生仿真器退出码 {result['native_exit_code']}。",
             f"- ELF：`{result['command'][1].removeprefix('--binary=')}`。",
             f"- 硬件 halted={sim.get('halted', '未知')}，fault={sim.get('fault', '未知')}。",
             f"- 宿主机耗时：{result['wall_seconds']:.3f} 秒。"]
    if sim:
        lines.append(f"- 仿真时间：{sim['simulation_time_ns']} ns，包含 ELF 装载和复位，使用测试台时间标尺。")
        if 'retired_instructions' in sim:
            lines.extend([f"- 全程序退休指令数：{sim['retired_instructions']}，由仿真器经 AXI 读取硬件 minstret，不需要 ELF 插桩。",
                          f"- core mcycle：{sim['core_cycles']}，从复位起到停机后冻结计数器，包含停机至门控的少量开销。"])
    if result.get('error'):
        lines.append('- 原因：' + result['error'])
    profile = data.get('profile')
    if profile and profile['completed']:
        lines.extend([f"- main 返回值：{profile['main_return']}。",
                      f"- main 区间：{profile['cycles']} cycles、{profile['retired_instructions']} 条退休指令，包含计数器测量开销以及 main 内的初始化和校验，不是纯算子延迟。"])
    elif profile:
        lines.append(f"- main 未正常返回：mcause=0x{profile['mcause']:x}，mepc=0x{profile['mepc']:x}，mtval=0x{profile['mtval']:x}。")
    arrays = data.get('arrays', {})
    validation = data.get('validation')
    if validation:
        a, b, c = (arrays[x]['values'] for x in ('a', 'b', 'c'))
        lines.extend(['', '输入和实际输出都从 RTL 执行后的 SRAM dump 读取；参考值由主机独立计算。', '',
                      '| index | a | b | actual c | reference | 校验 |',
                      '|---:|---:|---:|---:|---:|:---|'])
        for i in range(min(len(c), 256)):
            expected = validation['expected'][i]
            state = 'PASS' if c[i] == expected else 'FAIL'
            lines.append(f'| {i} | {a[i]} | {b[i]} | {c[i]} | {expected} | {state} |')
        lines.extend(['', f"全部校验：{len(c) - len(validation['mismatches'])}/{len(c)} 通过。"])
    else:
        for name, array in arrays.items():
            lines.extend(['', f"`{name}`，地址 0x{array['address']:08x}，格式 {array['dtype']}：", '',
                          str(array['values'][:256])])
    lines.extend(['', '程序原始 UART 输出：[console.bin](console.bin)。', '',
                  '完整机器数据：[run.json](run.json)；原生日志：[run.log](run.log)。', '',
                  '本版本只修改主机侧仿真入口以增加反馈；RVV RTL 未修改。'])
    Path(path).write_text('\n'.join(lines) + '\n')
