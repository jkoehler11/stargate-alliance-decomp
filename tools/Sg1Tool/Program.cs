using System.Globalization;
using System.Text;
using UELib;

namespace Sg1Tool;

/// <summary>
/// Front-end for everything this project derives from the shipped packages.
///
/// Two rules the code is built around:
///  1. Output is deterministic: rows sorted, "\n" endings, no timestamps, no absolute paths.
///  2. Nothing here writes game content into the repository. Decompiled script goes to a
///     git-ignored directory (src/UnrealScript by default); the CSVs carry names and counts only.
/// </summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length == 0 || args[0] is "-h" or "--help" or "help")
        {
            Usage();
            return args.Length == 0 ? 2 : 0;
        }

        var options = ParseOptions(args.Skip(1).ToArray());
        try
        {
            return args[0] switch
            {
                "inventory" => InventoryCommand.Run(
                    Require(options, "dump"), options.GetValueOrDefault("out", "inventory"),
                    options.GetValueOrDefault("package")),
                "decompile" => DecompileCommand.Run(
                    Require(options, "dump"), options.GetValueOrDefault("out", "src/UnrealScript"),
                    options.GetValueOrDefault("package")),
                "probe" => ProbeCommand.Run(
                    Require(options, "dump"),
                    options.GetValueOrDefault("package") ?? throw new ArgumentException("missing --package"),
                    options.GetValueOrDefault("init", "construct")!),
                _ => Unknown(args[0])
            };
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"error: {ex.Message}");
            // Full trace only on demand: the default message stays stable for CI logs.
            if (Environment.GetEnvironmentVariable("SG1_DEBUG") == "1")
                Console.Error.WriteLine(ex);
            return 1;
        }
    }

    private static int Unknown(string verb)
    {
        Console.Error.WriteLine($"unknown command '{verb}'");
        Usage();
        return 2;
    }

    private static void Usage()
    {
        Console.WriteLine("""
            Sg1Tool -- Stargate SG-1: The Alliance dump tooling

              Sg1Tool inventory --dump <dump-root> [--out inventory] [--package Engine]
                  Write packages.csv, classes.csv, natives.csv and package_parse.csv.

              Sg1Tool decompile --dump <dump-root> [--out src/UnrealScript] [--package StargateGame]
                  Reconstruct UnrealScript. Writes game-derived output: never commit it.

            <dump-root> is the extracted release tree (the directory containing README.txt).
            """);
    }

    private static string Require(Dictionary<string, string?> opts, string key)
    {
        var value = opts.GetValueOrDefault(key);
        if (string.IsNullOrWhiteSpace(value))
            throw new ArgumentException($"missing required --{key}");
        return value!;
    }

    private static Dictionary<string, string?> ParseOptions(string[] args)
    {
        var opts = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i < args.Length; i++)
        {
            if (!args[i].StartsWith("--"))
                throw new ArgumentException($"unexpected argument '{args[i]}'");
            var key = args[i][2..];
            string? value = null;
            if (key.Contains('='))
            {
                var parts = key.Split('=', 2);
                key = parts[0];
                value = parts[1];
            }
            else if (i + 1 < args.Length && !args[i + 1].StartsWith("--"))
            {
                value = args[++i];
            }

            opts[key] = value;
        }

        return opts;
    }
}

internal static class Dump
{
    /// <summary>Path to the game directory inside a dump root.</summary>
    public static string GameDir(string dumpRoot)
    {
        if (!Directory.Exists(dumpRoot))
            throw new DirectoryNotFoundException($"dump root not found: {dumpRoot}");

        var candidate = Path.Combine(dumpRoot, "Stargate_SG-1_-_The_Alliance-2005-12-15");
        var dir = Directory.Exists(candidate) ? candidate : dumpRoot;
        if (!Directory.Exists(Path.Combine(dir, "System")))
            throw new DirectoryNotFoundException(
                $"no System/ directory under '{dir}' -- point --dump at the extracted release tree");
        return dir;
    }

    public static IEnumerable<string> Packages(string dumpRoot)
    {
        return Directory.GetFiles(Path.Combine(GameDir(dumpRoot), "System"), "*.u",
                SearchOption.TopDirectoryOnly)
            .OrderBy(p => p, StringComparer.Ordinal);
    }

    public static UnrealPackage Load(string path)
    {
        var package = UnrealLoader.LoadPackage(path, FileAccess.Read);
        // Construct alone leaves every export undeserialized, so UELib cannot tell a UClass from a
        // property and OfType<UClass>() silently returns a handful of stubs. Deserialize+Link is
        // what actually materialises the object graph the inventory counts.
        package.InitializePackage(UnrealPackage.InitFlags.All);
        return package;
    }

    /// <summary>True for objects this package defines (positive export index), false for imports.</summary>
    public static bool IsExport(UELib.Core.UObject obj) => (int)obj > 0;

    public static string OuterClassName(UELib.Core.UObject obj)
    {
        var outer = obj.Outer;
        while (outer != null)
        {
            if (outer is UELib.Core.UClass cls) return cls.Name;
            outer = outer.Outer;
        }

        return "";
    }
}

internal static class FlagsText
{
    /// <summary>
    /// UELib's UnrealFlags&lt;T&gt;.ToString() dereferences a per-build flag map that is null for this
    /// build, so calling it throws. Decode the raw bits against the enum instead; anything left
    /// over is printed as hex rather than silently dropped.
    /// </summary>
    public static string Of<TEnum>(UELib.Flags.UnrealFlags<TEnum> flags) where TEnum : struct, Enum
    {
        try
        {
            var text = flags.ToString();
            if (!string.IsNullOrEmpty(text)) return text!;
        }
        catch (NullReferenceException)
        {
            // fall through to manual decoding
        }

        ulong raw = flags;
        if (raw == 0) return "None";
        var names = new List<string>();
        foreach (var value in Enum.GetValues<TEnum>())
        {
            var bit = Convert.ToUInt64(value);
            if (bit != 0 && (raw & bit) == bit)
            {
                names.Add(value.ToString()!);
                raw &= ~bit;
            }
        }

        if (raw != 0) names.Add($"0x{raw:X}");
        return names.Count == 0 ? $"0x{(ulong)flags:X}" : string.Join('|', names);
    }
}

internal static class Csv
{
    public static void Write(string path, string[] header, IEnumerable<object?[]> rows)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var sb = new StringBuilder();
        sb.Append(string.Join(',', header.Select(Escape))).Append('\n');
        foreach (var row in rows)
            sb.Append(string.Join(',', row.Select(Escape))).Append('\n');
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(false));
    }

    /// <summary>Sort key for a cell of any type (UName, enum, number) -- never throws.</summary>
    public static string SortKey(object? value) => Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";

    public static string Escape(object? value)
    {
        var s = value switch
        {
            null => "",
            IFormattable f => f.ToString(null, CultureInfo.InvariantCulture),
            _ => value.ToString() ?? ""
        };
        if (s.Contains('"') || s.Contains(',') || s.Contains('\n') || s.Contains('\r'))
            return '"' + s.Replace("\"", "\"\"") + '"';
        return s;
    }
}
