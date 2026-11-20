// Which functions read an instance offset: the reader side of a record whose writer is known.
//
// A serializer answers "what is written and in what order"; it cannot answer "what is this member for",
// because the meaning lives in the code that consumes it. That code reads the member at a fixed
// displacement (`mov EAX,[EBX+0x1358]`), which no call-graph query finds -- `Callers.java` walks edges, and
// a field read has none. This script walks every instruction of every function and records the ones with a
// scalar operand equal to a named displacement, so a member can be followed to its readers by offset.
//
// args: <offset[,offset...]|@file> <out.csv>
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.scalar.Scalar;
import java.io.*;
import java.util.*;

public class OffsetReaders extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: OffsetReaders.java <offset[,offset...]|@file> <out.csv>");
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
        // A displacement is written as a signed 32-bit value in some decodings and unsigned in others, so
        // the comparison is made on the unsigned value and the signed twin is accepted too.
        Set<Long> want = new HashSet<>();
        for (Long o : offsets) {
            want.add(o);
            want.add(o & 0xffffffffL);
        }

        PrintWriter out = new PrintWriter(new FileWriter(args[1]));
        out.println("offset,function,function_addr,instruction_addr,instruction");
        int rows = 0, funcs = 0;
        Set<Long> seenFunctions = new HashSet<>();
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        while (it.hasNext()) {
            Function fn = it.next();
            if (fn.isThunk()) continue;
            for (Instruction ins = getInstructionAt(fn.getEntryPoint());
                 ins != null && fn.getBody().contains(ins.getAddress());
                 ins = ins.getNext()) {
                for (int op = 0; op < ins.getNumOperands(); op++) {
                    Object[] objs = ins.getOpObjects(op);
                    for (Object o : objs) {
                        if (!(o instanceof Scalar)) continue;
                        long v = ((Scalar) o).getUnsignedValue() & 0xffffffffL;
                        if (!want.contains(v)) continue;
                        out.println(String.format("0x%x,%s,0x%08x,0x%08x,\"%s\"",
                                v, fn.getName(), fn.getEntryPoint().getOffset(), ins.getAddress().getOffset(),
                                ins.toString().replace("\"", "'")));
                        rows++;
                        if (seenFunctions.add(fn.getEntryPoint().getOffset())) funcs++;
                    }
                }
            }
        }
        out.close();
        println("offset readers: " + rows + " instruction(s) in " + funcs + " function(s) -> " + args[1]);
    }
}
