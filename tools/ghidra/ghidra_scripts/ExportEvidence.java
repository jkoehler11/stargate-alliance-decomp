// Export the call graph and per-function string references from the analysed XBE.
//
// Why: the 721 GNatives slots that no shipped package declares (PLAN 4.4) have no name and no
// caller -- the index is evidence that the address is a dispatch slot and nothing more. What can
// still be said about them is behavioural: which engine source file their callees come from (assert
// attribution from XbePost), which addresses outside any function they call, and which string
// literals they reference. tools/slot-evidence.py turns that into a per-slot record.
//
// Must run after XbePost so the callee names in the export are the final ones.
//
// args: <out-dir>
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.Reference;
import java.io.*;
import java.util.*;

public class ExportEvidence extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: ExportEvidence.java <out-dir>");
            return;
        }
        File dir = new File(args[0]);
        dir.mkdirs();
        FunctionManager fm = currentProgram.getFunctionManager();
        Listing listing = currentProgram.getListing();

        PrintWriter funcs = new PrintWriter(new FileWriter(new File(dir, "funcs.tsv")));
        PrintWriter calls = new PrintWriter(new FileWriter(new File(dir, "calls.tsv")));
        PrintWriter strings = new PrintWriter(new FileWriter(new File(dir, "strings.tsv")));
        funcs.println("# address\tname\tsize\tcallers\tcallees\texternal");
        calls.println("# caller\tsite\tcallee_addr\tcallee_name");
        strings.println("# func\tref_va\ttext");

        long nFuncs = 0, nCalls = 0, nStrings = 0, nExternal = 0;
        // Address order, so two runs over the same program produce identical files.
        for (Function fn : fm.getFunctions(true)) {
            Address entry = fn.getEntryPoint();
            TreeMap<String, Integer> calleeCounts = new TreeMap<>();
            int external = 0;
            for (Instruction ins = listing.getInstructionAt(fn.getBody().getMinAddress());
                 ins != null && fn.getBody().contains(ins.getAddress()); ins = ins.getNext()) {
                boolean isCall = ins.getFlowType().isCall();
                for (Reference ref : ins.getReferencesFrom()) {
                    if (isCall && ref.getReferenceType().isCall()) {
                        Address to = ref.getToAddress();
                        Function callee = fm.getFunctionAt(to);
                        String calleeName = callee != null ? callee.getName() : "";
                        calls.println(hex(entry.getOffset()) + "\t" + hex(ins.getAddress().getOffset())
                                + "\t" + hex(to.getOffset()) + "\t" + calleeName);
                        nCalls++;
                        if (callee == null) { external++; nExternal++; }
                        else calleeCounts.merge(calleeName, 1, Integer::sum);
                    } else if (ref.getReferenceType().isData()) {
                        String s = stringAt(ref.getToAddress());
                        if (s != null && !s.isEmpty()) {
                            strings.println(hex(entry.getOffset()) + "\t"
                                    + hex(ref.getToAddress().getOffset()) + "\t" + escape(s));
                            nStrings++;
                        }
                    }
                }
            }
            int callerCount = 0;
            Set<Function> callers = fn.getCallingFunctions(monitor);
            if (callers != null) callerCount = callers.size();
            funcs.println(hex(entry.getOffset()) + "\t" + fn.getName() + "\t"
                    + fn.getBody().getNumAddresses() + "\t" + callerCount + "\t"
                    + calleeCounts.size() + "\t" + external);
            nFuncs++;
        }
        funcs.close();
        calls.close();
        strings.close();
        println("evidence: " + nFuncs + " functions, " + nCalls + " direct calls ("
                + nExternal + " to addresses with no function), " + nStrings
                + " string references -> " + dir.getPath());
    }

    private static String hex(long v) {
        return String.format("0x%08x", v);
    }

    private static String escape(String s) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < s.length() && i < 200; i++) {
            char c = s.charAt(i);
            if (c == '\t') b.append("\\t");
            else if (c == '\n') b.append("\\n");
            else if (c == '\r') b.append("\\r");
            else if (c < 0x20 || c > 0x7e) b.append('?');
            else b.append(c);
        }
        if (s.length() > 200) b.append("...");
        return b.toString();
    }

    private String stringAt(Address at) {
        if (at == null) return null;
        Data data = getDataAt(at);
        if (data == null) return null;
        DataType dt = data.getDataType();
        if (dt == null) return null;
        try {
            StringDataInstance s = StringDataInstance.getStringDataInstance(data);
            return s == null ? null : s.getStringValue();
        } catch (Exception e) {
            return null;
        }
    }
}
