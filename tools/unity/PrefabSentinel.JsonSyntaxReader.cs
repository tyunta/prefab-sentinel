using System;
using System.Globalization;
using System.Text;

namespace PrefabSentinel
{
    internal sealed class JsonSyntaxReader
    {
        private readonly string _text;
        private readonly Func<Exception> _errorFactory;
        private int _index;

        internal JsonSyntaxReader(string text, Func<Exception> errorFactory)
        {
            _text = text;
            _errorFactory = errorFactory;
        }

        internal void ReadObject(Action<string> readMember)
        {
            Expect('{');
            SkipWhitespace();
            if (Take('}')) return;
            while (true)
            {
                string member = ReadString();
                Expect(':');
                readMember(member);
                SkipWhitespace();
                if (Take('}')) return;
                Expect(',');
            }
        }

        internal string ReadString()
        {
            SkipWhitespace();
            if (!Take('"')) Fail();
            var value = new StringBuilder();
            while (_index < _text.Length)
            {
                char current = _text[_index++];
                if (current == '"') return value.ToString();
                if (current < 0x20) Fail();
                if (current != '\\')
                {
                    value.Append(current);
                    continue;
                }
                if (_index >= _text.Length) Fail();
                char escaped = _text[_index++];
                switch (escaped)
                {
                    case '"': value.Append('"'); break;
                    case '\\': value.Append('\\'); break;
                    case '/': value.Append('/'); break;
                    case 'b': value.Append('\b'); break;
                    case 'f': value.Append('\f'); break;
                    case 'n': value.Append('\n'); break;
                    case 'r': value.Append('\r'); break;
                    case 't': value.Append('\t'); break;
                    case 'u': value.Append(ReadUnicode()); break;
                    default: Fail(); break;
                }
            }
            Fail();
            return string.Empty;
        }

        internal long ReadInteger()
        {
            string token = ReadNumber();
            long result = 0;
            if (token.IndexOf('.') >= 0
                || token.IndexOf('e') >= 0
                || token.IndexOf('E') >= 0
                || !long.TryParse(
                    token,
                    NumberStyles.AllowLeadingSign,
                    CultureInfo.InvariantCulture,
                    out result))
            {
                Fail();
            }
            return result;
        }

        internal void ReadTrue()
        {
            SkipWhitespace();
            ExpectLiteral("true");
        }

        internal void SkipValue(int depth = 0)
        {
            if (depth > 256) Fail();
            SkipWhitespace();
            char current = Peek();
            if (current == '"')
            {
                ReadString();
            }
            else if (current == '{')
            {
                ReadObject(_ => SkipValue(depth + 1));
            }
            else if (current == '[')
            {
                Expect('[');
                SkipWhitespace();
                if (Take(']')) return;
                while (true)
                {
                    SkipValue(depth + 1);
                    SkipWhitespace();
                    if (Take(']')) return;
                    Expect(',');
                }
            }
            else if (current == 't')
            {
                ExpectLiteral("true");
            }
            else if (current == 'f')
            {
                ExpectLiteral("false");
            }
            else if (current == 'n')
            {
                ExpectLiteral("null");
            }
            else
            {
                ReadNumber();
            }
        }

        internal void RequireEnd()
        {
            SkipWhitespace();
            if (_index != _text.Length) Fail();
        }

        private string ReadNumber()
        {
            SkipWhitespace();
            int start = _index;
            Take('-');
            if (Take('0'))
            {
                if (IsDigit(PeekOrNull())) Fail();
            }
            else
            {
                if (!IsDigitOneToNine(PeekOrNull())) Fail();
                _index++;
                while (IsDigit(PeekOrNull())) _index++;
            }
            if (Take('.'))
            {
                if (!IsDigit(PeekOrNull())) Fail();
                while (IsDigit(PeekOrNull())) _index++;
            }
            if (Take('e') || Take('E'))
            {
                if (!Take('+')) Take('-');
                if (!IsDigit(PeekOrNull())) Fail();
                while (IsDigit(PeekOrNull())) _index++;
            }
            return _text.Substring(start, _index - start);
        }

        private char ReadUnicode()
        {
            if (_index + 4 > _text.Length) Fail();
            string hex = _text.Substring(_index, 4);
            _index += 4;
            ushort value = 0;
            if (!ushort.TryParse(
                hex,
                NumberStyles.AllowHexSpecifier,
                CultureInfo.InvariantCulture,
                out value))
            {
                Fail();
            }
            return (char)value;
        }

        private void ExpectLiteral(string literal)
        {
            if (_index + literal.Length > _text.Length
                || string.CompareOrdinal(
                    _text, _index, literal, 0, literal.Length) != 0)
            {
                Fail();
            }
            _index += literal.Length;
        }

        private void Expect(char expected)
        {
            SkipWhitespace();
            if (!Take(expected)) Fail();
        }

        private bool Take(char expected)
        {
            if (_index < _text.Length && _text[_index] == expected)
            {
                _index++;
                return true;
            }
            return false;
        }

        private char Peek()
        {
            if (_index >= _text.Length) Fail();
            return _text[_index];
        }

        private char PeekOrNull()
        {
            return _index < _text.Length ? _text[_index] : '\0';
        }

        private void SkipWhitespace()
        {
            while (_index < _text.Length)
            {
                char current = _text[_index];
                if (current != ' '
                    && current != '\t'
                    && current != '\r'
                    && current != '\n')
                {
                    return;
                }
                _index++;
            }
        }

        private static bool IsDigit(char value)
        {
            return value >= '0' && value <= '9';
        }

        private static bool IsDigitOneToNine(char value)
        {
            return value >= '1' && value <= '9';
        }

        private void Fail()
        {
            throw _errorFactory();
        }
    }
}
