// Which functions read a struct displacement, and with what base register.
//
// `OffsetReaders.java` answers "does any instruction in this function carry the scalar 0x4c", which is right for a
// class whose identity is not in question but useless when it is: `[ESP+0x28]` is a stack slot, not a member, and
// `1,231` functions read `UStaticMeshInstance+0x4c` for that reason. A field has a *base*, so this script records
// the register the displacement is relative to alongside the displacement; a consumer can then keep only the
// accesses whose base is an object (not ESP/EBP), and intersect two offsets on one base -- "reads +0x4c of the same
// object whose colour array it also reads" is one function or a handful instead of all of them.
//
// `lea edx,[ebx+0x4c]` is the address of the member, not its value, and it is the shape a caller uses when it hands
// the member to a writer, so the access is recorded (`lea`) rather than dropped: the member's address being taken
// is evidence about which object the displacement belongs to.
//
// args: <offset[,offset...]|@file> <out.csv>
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import java.io.*;
import java.util.*;

public class FieldReaders extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: FieldReaders.java <offset[,offset...]|@file> <out.csv>");
            return;
        }
        List<Long> offsets = new ArrayList<>();
        if (args[0].startsWith("@")) {
            BufferedReader r = new BufferedReader(new FileReader(args[0].substring(1)));
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty() && !line.startsWith("#")) offsets.add(Long.decode(line));
            }
            r.close();
        } else {
            for (String a : args[0].split(",")) if (!a.trim().isEmpty()) offsets.add(Long.decode(a.trim()));
        }
        // Signed and unsigned spellings of the same 32-bit displacement both occur in the listings.
        Map<Long, Long> want = new HashMap<>();
        for (Long o : offsets) {
            want.put(o & 0xffffffffL, o);
        }

        PrintWriter out = new PrintWriter(new FileWriter(args[1]));
        out.println("offset,base,access,function,function_addr,instruction_addr,instruction");
        int rows = 0, funcs = 0;
        Set<Long> seenFunctions = new HashSet<>();
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        while (it.hasNext()) {
            Function fn = it.next();
            if (fn.isThunk()) continue;
            for (Instruction ins = getInstructionAt(fn.getEntryPoint());
                 ins != null && fn.getBody().contains(ins.getAddress());
                 ins = ins.getNext()) {
                String mnemonic = ins.getMnemonicString().toLowerCase();
                for (int op = 0; op < ins.getNumOperands(); op++) {
                    Object[] objs = ins.getOpObjects(op);
                    // A member access is a displacement in an operand that also names a register; the scalar alone
                    // is what OffsetReaders counts, and it also matches immediates and stack slots.
                    List<String> regs = new ArrayList<>();
                    List<Long> hits = new ArrayList<>();
                    for (Object o : objs) {
                        if (o instanceof Register) regs.add(((Register) o).getName());
                        else if (o instanceof Scalar) {
                            long v = ((Scalar) o).getUnsignedValue() & 0xffffffffL;
                            if (want.containsKey(v)) hits.add(v);
                        }
                    }
                    if (regs.isEmpty() || hits.isEmpty()) continue;
                    String base = String.join("+", regs);
                    String access;
                    if (mnemonic.startsWith("lea")) access = "addr";
                    else if (op == 0) access = "write";
                    else access = "read";
                    for (Long v : hits) {
                        out.println(String.format("0x%x,%s,%s,%s,0x%08x,0x%08x,\"%s\"",
                                v, base, access, fn.getName(), fn.getEntryPoint().getOffset(),
                                ins.getAddress().getOffset(), ins.toString().replace("\"", "'")));
                        rows++;
                        if (seenFunctions.add(fn.getEntryPoint().getOffset())) funcs++;
                    }
                }
            }
        }
        out.close();
        println("field readers: " + rows + " access(es) in " + funcs + " function(s) -> " + args[1]);
    }
}
